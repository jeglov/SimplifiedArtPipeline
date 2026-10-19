using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEngine;

namespace Artpipe
{
    /// <summary>
    /// Batchmode entry point:
    /// <c>Unity.exe -batchmode -projectPath unity -buildTarget Android
    /// -executeMethod Artpipe.Cli.Import --report PATH</c>.
    /// Use <c>artpipe unity import</c>, which also cross-checks the exit code
    /// against the report.
    ///
    /// Step 1 stub: runs the environment preflight and writes the report.
    /// Asset import arrives in later steps.
    ///
    /// Our own arguments use a double dash: Unity's arguments use one dash,
    /// so the two sets can never collide. Unity ignores arguments it doesn't know.
    /// </summary>
    public static class Cli
    {
        public static void Import()
        {
            int code;
            try
            {
                code = Run(Environment.GetCommandLineArgs());
            }
            catch (Exception e)
            {
                // An exception escaping -executeMethod makes Unity exit with 1,
                // which our contract reads as "asset failed". Catch it: a crash is 2.
                Debug.LogError("artpipe: unhandled exception: " + e);
                code = ExitCode.ToolError;
            }
            EditorApplication.Exit(code);
        }

        internal static int Run(string[] argv)
        {
            var args = CliArgs.Parse(argv);
            if (args.Error != null)
            {
                Debug.LogError("artpipe: " + args.Error);
                return ExitCode.ToolError;
            }

            var report = new ImportReport();
            report.Preflight.AddRange(Preflight.Run());
            if (report.Status == "error" && report.ToolError == null)
                report.ToolError = "Unity project preflight failed: see preflight[].";

            WriteReport(args.ReportPath, report.ToJson());
            Debug.Log($"artpipe: {report.Status.ToUpperInvariant()} {args.ReportPath}");
            return report.ExitCode;
        }

        public static void WriteReport(string path, string json)
        {
            var dir = Path.GetDirectoryName(Path.GetFullPath(path));
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
            // UTF-8 without BOM, LF only: same bytes as the Python reports.
            File.WriteAllText(path, json, new UTF8Encoding(false));
        }
    }

    /// <summary>Parses our <c>--name value</c> arguments out of Unity's command line.</summary>
    public sealed class CliArgs
    {
        public string ReportPath;
        public string Error;

        static readonly HashSet<string> Known = new HashSet<string> { "--report" };

        public static CliArgs Parse(IList<string> argv)
        {
            var a = new CliArgs();
            for (int i = 0; i < argv.Count; i++)
            {
                string arg = argv[i];
                if (!arg.StartsWith("--", StringComparison.Ordinal)) continue;  // Unity's own
                if (!Known.Contains(arg)) { a.Error = $"unknown argument {arg}"; return a; }
                if (i + 1 >= argv.Count || argv[i + 1].StartsWith("-", StringComparison.Ordinal))
                {
                    a.Error = $"{arg} needs a value";
                    return a;
                }
                a.ReportPath = argv[++i];
            }
            if (a.ReportPath == null) a.Error = "--report PATH is required";
            // Unity's working directory is the PROJECT folder, not the caller's,
            // so a relative path would silently land inside unity/. Refuse it.
            else if (!Path.IsPathFullyQualified(a.ReportPath))
                a.Error = $"--report must be an absolute path, got {a.ReportPath}";
            return a;
        }
    }
}
