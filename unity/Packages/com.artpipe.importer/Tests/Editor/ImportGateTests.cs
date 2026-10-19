using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace Artpipe.Tests
{
    /// <summary>Golden FBX + report pairs, built by `artpipe unity-fixtures` from the Maya fixtures.</summary>
    public static class Goldens
    {
        public const string Folder = "Packages/com.artpipe.importer/Tests/Editor/Fixtures~";

        public static IEnumerable<string> Names() =>
            Directory.GetFiles(Folder, "*.fbx").Select(Path.GetFileNameWithoutExtension)
                .OrderBy(n => n, StringComparer.Ordinal);

        public static string Fbx(string name) => Path.Combine(Folder, name + ".fbx");
    }

    public class ImportGateTests
    {
        string _tmp;
        ConventionsLock _conventions;

        [SetUp]
        public void SetUp()
        {
            _conventions = ConventionsLock.Load();
            _tmp = Path.Combine(Path.GetTempPath(), "artpipe-gate-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(_tmp);
        }

        [TearDown]
        public void TearDown() => Directory.Delete(_tmp, true);

        /// <summary>Copy a golden pair to a temp folder; returns the FBX path.</summary>
        string Copy(string name, bool withReport = true)
        {
            var fbx = Path.Combine(_tmp, name + ".fbx");
            File.Copy(Goldens.Fbx(name), fbx);
            if (withReport) File.Copy(ImportGate.SidecarPath(Goldens.Fbx(name)), ImportGate.SidecarPath(fbx));
            return fbx;
        }

        static void EditReport(string fbx, Action<JObject> change)
        {
            var path = ImportGate.SidecarPath(fbx);
            var doc = JObject.Parse(File.ReadAllText(path));
            change(doc);
            File.WriteAllText(path, doc.ToString());
        }

        List<string> Failed(string fbx) =>
            ImportGate.Evaluate(fbx, _conventions).Where(r => !r.Passed).Select(r => r.Id).ToList();

        [Test]
        public void EveryGoldenPasses()
        {
            var names = Goldens.Names().ToList();
            Assert.IsNotEmpty(names, "no goldens: run artpipe unity-fixtures");
            foreach (var name in names)
                CollectionAssert.IsEmpty(Failed(Copy(name)), name);
        }

        [Test]
        public void MissingReportIsRefused()
        {
            var results = ImportGate.Evaluate(Copy("SM_Clean", withReport: false), _conventions);
            Assert.AreEqual(1, results.Count, "nothing else can be judged without a report");
            Assert.AreEqual("gate.report_present", results[0].Id);
            Assert.IsFalse(results[0].Passed);
        }

        [Test]
        public void EditedFbxIsRefused()
        {
            // Boundary: ONE byte appended after validation.
            var fbx = Copy("SM_Clean");
            File.AppendAllText(fbx, " ");
            CollectionAssert.AreEqual(new[] { "gate.hash_match" }, Failed(fbx));
        }

        [Test]
        public void FailedReportIsRefusedAndSaysWhy()
        {
            var fbx = Copy("SM_Clean");
            EditReport(fbx, d =>
            {
                d["status"] = "fail";
                var r = (JObject)d["results"][0];
                r["passed"] = false;
                r["offenders"] = new JArray("|SM_Clean");
            });
            var gate = ImportGate.Evaluate(fbx, _conventions).Single(r => r.Id == "gate.report_passed");
            Assert.IsFalse(gate.Passed);
            StringAssert.Contains("failed: ", gate.Message);
        }

        [Test]
        public void ReportOfTheSceneNotTheExportIsRefused()
        {
            // A plain `artpipe validate` report has no export section.
            var fbx = Copy("SM_Clean");
            EditReport(fbx, d => { d["export"] = null; d["exported_from"] = null; });
            CollectionAssert.AreEqual(new[] { "gate.export_settings" }, Failed(fbx));
        }

        [Test]
        public void OtherExportSettingsVersionIsRefused()
        {
            var fbx = Copy("SM_Clean");
            EditReport(fbx, d => d["export"]["settings_version"] = _conventions.FbxExport.SettingsVersion + 1);
            CollectionAssert.AreEqual(new[] { "gate.export_settings" }, Failed(fbx));
        }

        [Test]
        public void UnknownAssetClassIsRefused()
        {
            var fbx = Copy("SM_Clean");
            EditReport(fbx, d => d["asset_class"] = "prop_tiny");
            CollectionAssert.AreEqual(new[] { "gate.asset_class" }, Failed(fbx));
        }

        [TestCase("1.3")]
        [TestCase("1.1")]
        public void UnsupportedSchemaVersionIsRefused(string version)
        {
            var fbx = Copy("SM_Clean");
            EditReport(fbx, d => d["schema_version"] = version);
            var results = ImportGate.Evaluate(fbx, _conventions);
            Assert.AreEqual("gate.report_readable", results.Last().Id);
            StringAssert.Contains("schema_version " + version, results.Last().Message);
        }

        [Test]
        public void ReportWithUnknownKeyIsRefused()
        {
            var fbx = Copy("SM_Clean");
            EditReport(fbx, d => d["approved_by"] = "someone");
            StringAssert.Contains("approved_by", ImportGate.Evaluate(fbx, _conventions).Last().Message);
        }

        [Test]
        public void ReportWithMissingKeyIsRefused()
        {
            var fbx = Copy("SM_Clean");
            EditReport(fbx, d => d.Remove("tool_error"));  // nullable, but must be present
            Assert.AreEqual("gate.report_readable", ImportGate.Evaluate(fbx, _conventions).Last().Id);
        }

        [Test]
        public void GarbageReportIsRefused()
        {
            var fbx = Copy("SM_Clean");
            File.WriteAllText(ImportGate.SidecarPath(fbx), "{ not json");
            Assert.IsFalse(ImportGate.Evaluate(fbx, _conventions).Last().Passed);
        }
    }

    /// <summary>
    /// Real imports into the project, cleaned up after each test: creates the
    /// folders it needs (e.g. Assets/Art/prop_small/__artpipe_tests__) and
    /// deletes exactly the folders it created, never anything that was there.
    /// </summary>
    public sealed class Sandbox
    {
        public const string Marker = "__artpipe_tests__";
        readonly List<string> _created = new List<string>();
        public readonly ConventionsLock Conventions = ConventionsLock.Load();

        readonly List<string> _files = new List<string>();

        /// <summary>A test folder inside &lt;root&gt;/&lt;folderClass&gt;/; "" = the root itself (files there are cleaned up one by one).</summary>
        public string ClassFolder(string folderClass) =>
            Folder(folderClass == "" ? Conventions.UnityImport.Root
                                     : $"{Conventions.UnityImport.Root}/{folderClass}/{Marker}");

        public string Folder(string assetFolder)
        {
            var parts = assetFolder.Split('/');
            for (int i = 2; i <= parts.Length; i++)
            {
                var path = string.Join("/", parts.Take(i));
                if (AssetDatabase.IsValidFolder(path)) continue;
                AssetDatabase.CreateFolder(string.Join("/", parts.Take(i - 1)), parts[i - 1]);
                _created.Add(path);
            }
            return assetFolder;
        }

        /// <summary>Delete this asset path on Dispose.</summary>
        public void Track(string assetPath) => _files.Add(assetPath);

        public string Put(string golden, string folder, bool withReport = true)
        {
            var fbx = $"{folder}/{golden}.fbx";
            File.Copy(Goldens.Fbx(golden), fbx, true);
            _files.Add(fbx);
            if (withReport)
            {
                File.Copy(ImportGate.SidecarPath(Goldens.Fbx(golden)), ImportGate.SidecarPath(fbx), true);
                _files.Add(ImportGate.SidecarPath(fbx));
            }
            return fbx;
        }

        /// <summary>
        /// Refresh and load. expectError: a regex at least one import error must
        /// match; any other error fails the test. Errors are captured instead of
        /// LogAssert.Expect'ed one by one because an FBX that arrives together with
        /// its sidecar is imported twice (the sidecar is a declared dependency),
        /// so the same error can legitimately appear more than once.
        /// </summary>
        public static GameObject Import(string path, string expectError = null)
        {
            var errors = new List<string>();
            Application.LogCallback capture = (msg, _, type) =>
            {
                if (type == LogType.Error || type == LogType.Exception) errors.Add(msg);
            };
            Application.logMessageReceived += capture;
            LogAssert.ignoreFailingMessages = true;
            try { AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport); }
            finally
            {
                Application.logMessageReceived -= capture;
                LogAssert.ignoreFailingMessages = false;
            }
            if (expectError == null)
                CollectionAssert.IsEmpty(errors, "unexpected import errors");
            else
            {
                var rx = new Regex(expectError);
                Assert.IsTrue(errors.Any(rx.IsMatch), $"expected an error matching /{expectError}/, got: {string.Join(" | ", errors)}");
                CollectionAssert.IsEmpty(errors.Where(e => !rx.IsMatch(e)), "other import errors");
            }
            return AssetDatabase.LoadAssetAtPath<GameObject>(path);
        }

        public void Dispose()
        {
            foreach (var f in _files) AssetDatabase.DeleteAsset(f);
            _files.Clear();
            for (int i = _created.Count - 1; i >= 0; i--) AssetDatabase.DeleteAsset(_created[i]);
            _created.Clear();
        }
    }

    /// <summary>The gate inside Unity's real import (AssetPostprocessor), under the managed root.</summary>
    public class ModelGatePostprocessorTests
    {
        Sandbox _box;

        [SetUp] public void SetUp() => _box = new Sandbox();
        [TearDown] public void TearDown() => _box.Dispose();

        [Test]
        public void GatedFbxImportsNormally()
        {
            var go = Sandbox.Import(_box.Put("SM_Clean", _box.ClassFolder("prop_small")));
            Assert.IsNotNull(go);
            Assert.IsFalse(ModelPostprocessor.IsInert(go));
        }

        [Test]
        public void UngatedFbxIsMadeInertAndFlagged()
        {
            var fbx = _box.Put("SM_Clean", _box.ClassFolder("prop_small"), withReport: false);
            var go = Sandbox.Import(fbx, "artpipe gate refused .*gate.report_present");
            Assert.IsNotNull(go, "the asset still exists (an import can't be cancelled)...");
            Assert.IsTrue(ModelPostprocessor.IsInert(go), "...but it has no renderers");
        }

        [Test]
        public void AddingTheReportLaterReimportsTheFbx()
        {
            // The sidecar is a declared import dependency: adding it must rerun the gate.
            var folder = _box.ClassFolder("prop_small");
            var fbx = _box.Put("SM_Clean", folder, withReport: false);
            Assert.IsTrue(ModelPostprocessor.IsInert(Sandbox.Import(fbx, "artpipe gate refused")));

            _box.Put("SM_Clean", folder, withReport: true);
            Assert.IsFalse(ModelPostprocessor.IsInert(Sandbox.Import(fbx)));
        }

        [Test]
        public void FbxInTheWrongClassFolderIsRefused()
        {
            // Validated as prop_small, dropped into pickup/: budgets would be wrong (backlog #6).
            var fbx = _box.Put("SM_Clean", _box.ClassFolder("pickup"));
            Assert.IsTrue(ModelPostprocessor.IsInert(Sandbox.Import(fbx, "gate.asset_class: .*pickup.*prop_small")));
        }

        [Test]
        public void FbxDirectlyInTheRootIsRefused()
        {
            var fbx = _box.Put("SM_Clean", _box.ClassFolder(""));
            Assert.IsTrue(ModelPostprocessor.IsInert(Sandbox.Import(fbx, "gate.asset_class: The FBX must sit in its class folder")));
        }

        [Test]
        public void FbxOutsideTheManagedRootIsLeftAlone()
        {
            var fbx = _box.Put("SM_Clean", _box.Folder("Assets/" + Sandbox.Marker), withReport: false);
            Assert.IsFalse(ModelPostprocessor.IsInert(Sandbox.Import(fbx)));
        }
    }
}
