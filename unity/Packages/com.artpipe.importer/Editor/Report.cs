using System.Collections.Generic;
using System.Linq;

namespace Artpipe
{
    /// <summary>Exit codes, same contract as the Phase 1 Maya CLI.</summary>
    public static class ExitCode
    {
        public const int Pass = 0;      // everything passed
        public const int Fail = 1;      // an ASSET is bad: actionable, fix the asset
        public const int ToolError = 2; // the TOOL broke: retry or alert a human
    }

    public static class Severity
    {
        public const string Error = "error";
        public const string Warning = "warning";
        public const string Info = "info";
    }

    /// <summary>
    /// One check result. Same shape as Phase 1:
    /// <c>{id, passed, severity, metrics, message, fixable, offenders}</c>.
    /// </summary>
    public sealed class CheckResult
    {
        public string Id;
        public bool Passed;
        public string Severity;
        public string Message;
        public bool Fixable;
        public SortedDictionary<string, object> Metrics = new SortedDictionary<string, object>();
        public List<string> Offenders = new List<string>();

        public bool Blocking => !Passed && Severity == Artpipe.Severity.Error;

        public Dictionary<string, object> ToDict() => new Dictionary<string, object>
        {
            ["id"] = Id,
            ["passed"] = Passed,
            ["severity"] = Severity,
            ["message"] = Message,
            ["fixable"] = Fixable,
            ["metrics"] = new Dictionary<string, object>(Metrics),
            ["offenders"] = Offenders.Distinct().OrderBy(o => o, System.StringComparer.Ordinal).Cast<object>().ToList(),
        };
    }

    /// <summary>One asset in the Unity report: its project path and every result about it.</summary>
    public sealed class AssetReport
    {
        public string Path;          // Assets/...
        public string Kind;          // "model" | "texture"
        public string AssetClass;    // folder class, null outside class folders
        public bool Imported;        // false: refused before entering the project
        public string SourcePath;    // import mode: where it came from (repo-relative)
        public string SourceSha256;
        public string Prefab;        // Prefab Variant of a clean model (import mode)
        public List<string> Materials = new List<string>();  // shared .mat files a model's FBX materials map to
        public List<CheckResult> Results = new List<CheckResult>();

        public bool Passed => !Results.Any(r => r.Blocking);

        public Dictionary<string, object> ToDict() => new Dictionary<string, object>
        {
            ["path"] = Path,
            ["kind"] = Kind,
            ["asset_class"] = AssetClass,
            ["status"] = Passed ? "pass" : "fail",
            ["imported"] = Imported,
            ["prefab"] = Prefab,
            ["materials"] = Materials.OrderBy(m => m, System.StringComparer.Ordinal).Cast<object>().ToList(),
            ["source"] = SourcePath == null ? null : new Dictionary<string, object>
            {
                ["path"] = SourcePath,
                ["sha256"] = SourceSha256,
            },
            // Results keep their fixed check order (part of the contract), not sorted.
            ["results"] = Results.Select(r => (object)r.ToDict()).ToList(),
        };
    }

    /// <summary>
    /// The Unity-side report (contract: conventions/unity_report.schema.json).
    /// Deterministic: assets sorted by path, no timestamps, canonical JSON.
    /// </summary>
    public sealed class ImportReport
    {
        public const string SchemaVersion = "1.1";
        public const string Tool = "artpipe.unity 0.2.0";

        public string Mode = "verify";
        public List<CheckResult> Preflight = new List<CheckResult>();
        public List<AssetReport> Assets = new List<AssetReport>();
        public string ToolError;

        public string Status =>
            ToolError != null || Preflight.Any(r => r.Blocking) ? "error"
            : Assets.Any(a => !a.Passed) ? "fail"
            : "pass";

        public int ExitCode => Status == "pass" ? Artpipe.ExitCode.Pass
            : Status == "fail" ? Artpipe.ExitCode.Fail
            : Artpipe.ExitCode.ToolError;

        public string ToJson()
        {
            var assets = Assets.OrderBy(a => a.Path, System.StringComparer.Ordinal).ToList();
            var results = assets.SelectMany(a => a.Results).ToList();
            return CanonicalJson.Serialize(new Dictionary<string, object>
            {
                ["schema_version"] = SchemaVersion,
                ["tool"] = Tool,
                ["mode"] = Mode,
                ["status"] = Status,
                ["tool_error"] = ToolError,
                ["preflight"] = Preflight.OrderBy(r => r.Id, System.StringComparer.Ordinal)
                    .Select(r => (object)r.ToDict()).ToList(),
                ["assets"] = assets.Select(a => (object)a.ToDict()).ToList(),
                ["summary"] = new Dictionary<string, object>
                {
                    ["assets"] = assets.Count,
                    ["passed"] = assets.Count(a => a.Passed),
                    ["failed"] = assets.Count(a => !a.Passed),
                    ["errors"] = results.Count(r => r.Blocking),
                    ["warnings"] = results.Count(r => !r.Passed && r.Severity == Severity.Warning),
                    ["preflight_failed"] = Preflight.Count(r => !r.Passed),
                },
            });
        }
    }
}
