using System.Collections.Generic;
using System.Linq;
using NUnit.Framework;

namespace Artpipe.Tests
{
    /// <summary>Step 1: the harness itself (JSON, arguments, project preflight).</summary>
    public class CanonicalJsonTests
    {
        [Test]
        public void MatchesPythonJsonDumpsByteForByte()
        {
            // Expected string = Python:
            // json.dumps({"b": [1, 2.5, None], "a": {"z": True, "y": "q\"\n\u00e9"}, "e": [], "f": {}},
            //            indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            var value = new Dictionary<string, object>
            {
                ["b"] = new List<object> { 1, 2.5, null },
                ["a"] = new Dictionary<string, object> { ["z"] = true, ["y"] = "q\"\n\u00e9" },
                ["e"] = new List<object>(),
                ["f"] = new Dictionary<string, object>(),
            };
            const string expected =
                "{\n" +
                "  \"a\": {\n" +
                "    \"y\": \"q\\\"\\n\u00e9\",\n" +
                "    \"z\": true\n" +
                "  },\n" +
                "  \"b\": [\n" +
                "    1,\n" +
                "    2.5,\n" +
                "    null\n" +
                "  ],\n" +
                "  \"e\": [],\n" +
                "  \"f\": {}\n" +
                "}\n";
            Assert.AreEqual(expected, CanonicalJson.Serialize(value));
        }

        [Test]
        public void KeysSortOrdinallyNotByCulture()
        {
            // Ordinal: uppercase before lowercase, "_" (0x5F) between them. Python agrees.
            var json = CanonicalJson.Serialize(new Dictionary<string, object> { ["b"] = 1, ["_"] = 1, ["B"] = 1 });
            Assert.Less(json.IndexOf("\"B\""), json.IndexOf("\"_\""));
            Assert.Less(json.IndexOf("\"_\""), json.IndexOf("\"b\""));
        }

        [TestCase(1.0, "1.0")]
        [TestCase(0.1, "0.1")]
        [TestCase(-2.0, "-2.0")]
        [TestCase(1e-05, "1e-05")]
        public void DoublesFormatLikePython(double d, string expected)
        {
            Assert.AreEqual(expected + "\n", CanonicalJson.Serialize(d));
        }

        [Test]
        public void ControlCharactersAreEscaped()
        {
            Assert.AreEqual("\"\\u0001\"\n", CanonicalJson.Serialize("\u0001"));
        }
    }

    public class CliArgsTests
    {
        [Test]
        public void IgnoresUnityArgumentsAndReadsOurs()
        {
            var a = CliArgs.Parse(new[] { "Unity.exe", "-batchmode", "-projectPath", "x", "--report", "C:/out/r.json" });
            Assert.IsNull(a.Error);
            Assert.AreEqual("C:/out/r.json", a.ReportPath);
        }

        [Test]
        public void RelativeReportPathIsRefused()
        {
            // Probed: Unity's working directory is the project folder, so
            // "out/r.json" would be written to unity/out/r.json.
            Assert.AreEqual("--report must be an absolute path, got out/r.json",
                CliArgs.Parse(new[] { "--report", "out/r.json" }).Error);
        }

        [Test]
        public void ReportIsRequired()
        {
            Assert.AreEqual("--report PATH is required", CliArgs.Parse(new[] { "Unity.exe", "-batchmode" }).Error);
        }

        [Test]
        public void ReportWithoutValueIsAnError()
        {
            // Boundary: flag present as the very last argument.
            Assert.AreEqual("--report needs a value", CliArgs.Parse(new[] { "Unity.exe", "--report" }).Error);
            // ...or followed by another flag instead of a value.
            Assert.AreEqual("--report needs a value", CliArgs.Parse(new[] { "--report", "-quit" }).Error);
        }

        [Test]
        public void UnknownDoubleDashArgumentIsAnError()
        {
            // A typo must not be silently ignored: the agent would never learn about it.
            Assert.AreEqual("unknown argument --reprot", CliArgs.Parse(new[] { "--reprot", "x" }).Error);
        }
    }

    public class CliImportArgsTests
    {
        [Test]
        public void ReadsClassAndRepeatedFiles()
        {
            var a = CliArgs.Parse(new[] { "--report", "C:/r.json", "--class", "prop_small", "--file", "C:/a/SM_A.fbx", "--file", "C:/a/T_A_D.png" });
            Assert.IsNull(a.Error);
            Assert.AreEqual("prop_small", a.AssetClass);
            CollectionAssert.AreEqual(new[] { "C:/a/SM_A.fbx", "C:/a/T_A_D.png" }, a.Files);
        }

        [Test]
        public void RelativeInputFileIsRefused()
        {
            StringAssert.StartsWith("--file must be an absolute path",
                CliArgs.Parse(new[] { "--report", "C:/r.json", "--file", "out/SM_A.fbx" }).Error);
        }

        [Test]
        public void GuidIsDerivedFromThePathOnly()
        {
            var a = Cli.DeterministicGuid("Assets/Art/prop_small/SM_A.fbx");
            Assert.AreEqual(a, Cli.DeterministicGuid("Assets/Art/prop_small/SM_A.fbx"), "same path, same GUID, any machine");
            Assert.AreNotEqual(a, Cli.DeterministicGuid("Assets/Art/prop_small/SM_B.fbx"));
            StringAssert.IsMatch("^[0-9a-f]{32}$", a);
        }

        [Test]
        public void AnyFailedAssetMakesTheRunFail()
        {
            var report = new ImportReport();
            report.Assets.Add(new AssetReport { Path = "Assets/Art/x/a.fbx", Kind = "model" });
            Assert.AreEqual(ExitCode.Pass, report.ExitCode);
            report.Assets[0].Results.Add(new CheckResult { Id = "unity.vertex_budget", Passed = false, Severity = Severity.Error });
            Assert.AreEqual("fail", report.Status);
            Assert.AreEqual(ExitCode.Fail, report.ExitCode);
        }

        [Test]
        public void ToolErrorWinsOverAssetFailure()
        {
            var report = new ImportReport { ToolError = "unknown --class" };
            report.Assets.Add(new AssetReport { Path = "Assets/Art/x/a.fbx", Kind = "model",
                Results = { new CheckResult { Id = "gate.hash_match", Passed = false, Severity = Severity.Error } } });
            Assert.AreEqual(ExitCode.ToolError, report.ExitCode);
        }
    }

    public class PreflightTests
    {
        [Test]
        public void ProjectIsSetUpForTheImporter()
        {
            var failed = Preflight.Run().Where(r => !r.Passed).Select(r => r.Id + ": " + r.Message).ToList();
            Assert.IsEmpty(failed, string.Join("\n", failed));
        }

        [Test]
        public void ReportStatusIsErrorWhenPreflightFails()
        {
            var report = new ImportReport();
            report.Preflight.Add(new CheckResult { Id = "env.x", Passed = false, Severity = Severity.Error });
            Assert.AreEqual("error", report.Status);
            Assert.AreEqual(ExitCode.ToolError, report.ExitCode);
        }

        [Test]
        public void FailedWarningDoesNotBlock()
        {
            var report = new ImportReport();
            report.Preflight.Add(new CheckResult { Id = "env.x", Passed = false, Severity = Severity.Warning });
            Assert.AreEqual("pass", report.Status);
        }
    }
}
