using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace Artpipe
{
    /// <summary>
    /// Decides whether an FBX may enter the project: only with a sidecar Maya
    /// report (<c>X.fbx.report.json</c>, written by <c>artpipe validate --export</c>)
    /// that passed, describes THIS file's bytes, and was exported with the
    /// settings the conventions name.
    ///
    /// A pure function of its inputs (FBX bytes, sidecar bytes, conventions),
    /// which is what lets Unity's import cache store the verdict safely
    /// (ModelGatePostprocessor declares all three as import dependencies).
    /// </summary>
    public static class ImportGate
    {
        public static string SidecarPath(string fbxPath) => fbxPath + ".report.json";

        /// <summary>
        /// Gate results in a fixed order. Stops after the first result that makes later ones meaningless.
        /// folderClass: the class folder the FBX sits in (backlog #6), "" for none, null to skip that check.
        /// </summary>
        public static List<CheckResult> Evaluate(string fbxPath, ConventionsLock conventions, string folderClass = null)
        {
            var results = new List<CheckResult>();
            var sidecar = SidecarPath(fbxPath);
            var sidecarName = Path.GetFileName(sidecar);

            bool present = File.Exists(sidecar);
            results.Add(Result("gate.report_present", present, fbxPath,
                present ? $"{sidecarName} found."
                        : $"No {sidecarName} next to the FBX. Only files exported by `artpipe validate --export` may be imported."));
            if (!present) return results;

            MayaReport report = null;
            string why = null;
            try { report = MayaReport.Parse(File.ReadAllText(sidecar)); }
            catch (ReportException e) { why = e.Message; }
            results.Add(Result("gate.report_readable", report != null, fbxPath,
                report != null ? "Report matches the contract." : $"{sidecarName} {why}"));
            if (report == null) return results;

            bool passed = report.Status == "pass";
            var failing = report.Results.Where(r => !r.Passed && r.Severity == Severity.Error)
                .Select(r => r.Id).OrderBy(i => i, System.StringComparer.Ordinal).ToList();
            results.Add(Result("gate.report_passed", passed, fbxPath,
                passed ? "Maya validation passed."
                       : $"Maya validation status is {report.Status}"
                         + (failing.Count > 0 ? $" (failed: {string.Join(", ", failing)})" : "")
                         + (report.ToolError != null ? $" (tool error: {report.ToolError})" : "") + "."));

            var actual = ConventionsLock.Sha256(fbxPath);
            bool match = actual == report.Source.Sha256;
            results.Add(Result("gate.hash_match", match, fbxPath,
                match ? "The FBX is byte-identical to the file Maya validated."
                      : "The FBX differs from the file the report describes (edited or re-exported after validation). Re-run `artpipe validate --export`.",
                new Dictionary<string, object> { ["fbx_sha256"] = actual, ["report_sha256"] = report.Source.Sha256 }));

            int want = conventions.FbxExport.SettingsVersion;
            bool exported = report.Export != null && report.ExportedFrom != null;
            bool settingsOk = exported && report.Export.SettingsVersion == want;
            results.Add(Result("gate.export_settings", settingsOk, fbxPath,
                !exported ? "The report is not an export report (no export section): validate the FBX with `artpipe validate --export`."
                : settingsOk ? $"Exported with FBX settings version {want}."
                : $"Exported with FBX settings version {report.Export.SettingsVersion}, the conventions require {want}. Re-export.",
                new Dictionary<string, object> { ["required"] = want, ["found"] = exported ? (object)report.Export.SettingsVersion : null }));

            bool knownClass = conventions.Classes.ContainsKey(report.AssetClass);
            bool inFolder = folderClass == null || folderClass == report.AssetClass;
            var root = conventions.UnityImport.Root;
            results.Add(Result("gate.asset_class", knownClass && inFolder, fbxPath,
                !knownClass ? $"Asset class '{report.AssetClass}' is not in the conventions."
                : !inFolder ? (folderClass == ""
                    ? $"The FBX must sit in its class folder: {root}/{report.AssetClass}/."
                    : $"The FBX is in {root}/{folderClass}/ but was validated as {report.AssetClass}: move it to {root}/{report.AssetClass}/ or re-validate with --class {folderClass}.")
                : $"Asset class {report.AssetClass}.",
                new Dictionary<string, object> { ["report_class"] = report.AssetClass, ["folder_class"] = folderClass }));
            return results;
        }

        static CheckResult Result(string id, bool passed, string fbxPath, string message,
                                  Dictionary<string, object> metrics = null)
        {
            var r = new CheckResult { Id = id, Passed = passed, Severity = Severity.Error, Message = message, Fixable = false };
            if (metrics != null)
                foreach (var kv in metrics) r.Metrics[kv.Key] = kv.Value;
            if (!passed) r.Offenders.Add(fbxPath);
            return r;
        }
    }
}
