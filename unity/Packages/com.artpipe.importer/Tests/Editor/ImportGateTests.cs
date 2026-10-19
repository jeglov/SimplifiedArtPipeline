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

    /// <summary>The gate inside Unity's real import (AssetPostprocessor), under the managed root.</summary>
    public class ModelGatePostprocessorTests
    {
        string _root;          // e.g. Assets/Art
        string _dir;           // Assets/Art/__artpipe_tests__
        bool _createdRoot;
        string _outside = "Assets/__artpipe_tests_outside__";

        [SetUp]
        public void SetUp()
        {
            _root = ConventionsLock.Load().UnityImport.Root;
            _createdRoot = !AssetDatabase.IsValidFolder(_root);
            Directory.CreateDirectory(_root + "/__artpipe_tests__");
            Directory.CreateDirectory(_outside);
            _dir = _root + "/__artpipe_tests__";
            AssetDatabase.Refresh();
        }

        [TearDown]
        public void TearDown()
        {
            AssetDatabase.DeleteAsset(_dir);
            AssetDatabase.DeleteAsset(_outside);
            if (_createdRoot) AssetDatabase.DeleteAsset(_root);
        }

        static void Put(string name, string folder, bool withReport)
        {
            File.Copy(Goldens.Fbx(name), $"{folder}/{name}.fbx", true);
            if (withReport)
                File.Copy(ImportGate.SidecarPath(Goldens.Fbx(name)), $"{folder}/{name}.fbx.report.json", true);
        }

        static GameObject Import(string path)
        {
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            return AssetDatabase.LoadAssetAtPath<GameObject>(path);
        }

        [Test]
        public void GatedFbxImportsNormally()
        {
            Put("SM_Clean", _dir, withReport: true);
            var go = Import(_dir + "/SM_Clean.fbx");
            Assert.IsNotNull(go);
            Assert.IsFalse(ModelGatePostprocessor.Refused(go));
        }

        [Test]
        public void UngatedFbxIsMadeInertAndFlagged()
        {
            Put("SM_Clean", _dir, withReport: false);
            LogAssert.Expect(LogType.Error, new Regex("artpipe gate refused .*gate.report_present"));
            var go = Import(_dir + "/SM_Clean.fbx");
            Assert.IsNotNull(go, "the asset still exists (an import can't be cancelled)...");
            Assert.IsTrue(ModelGatePostprocessor.Refused(go), "...but it has no renderers");
        }

        [Test]
        public void AddingTheReportLaterReimportsTheFbx()
        {
            // The sidecar is a declared import dependency: adding it must rerun the gate.
            Put("SM_Clean", _dir, withReport: false);
            LogAssert.Expect(LogType.Error, new Regex("artpipe gate refused"));
            Assert.IsTrue(ModelGatePostprocessor.Refused(Import(_dir + "/SM_Clean.fbx")));

            Put("SM_Clean", _dir, withReport: true);
            Assert.IsFalse(ModelGatePostprocessor.Refused(Import(_dir + "/SM_Clean.fbx")));
        }

        [Test]
        public void FbxOutsideTheManagedRootIsLeftAlone()
        {
            Put("SM_Clean", _outside, withReport: false);
            var go = Import(_outside + "/SM_Clean.fbx");
            Assert.IsFalse(ModelGatePostprocessor.Refused(go));
            LogAssert.NoUnexpectedReceived();
        }
    }
}
