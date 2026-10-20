using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using UnityEditor;
using UnityEngine;

namespace Artpipe
{
    /// <summary>
    /// Batchmode entry point (use `artpipe unity import|verify`, which also
    /// cross-checks the exit code against the report):
    ///
    ///   import: -executeMethod Artpipe.Cli.Import --report ABS --class C --file ABS [--file ABS ...]
    ///     Each FBX is gated OUTSIDE the project first (sidecar report, hash,
    ///     settings, class): refused files never enter Assets/. Accepted files
    ///     are copied to &lt;root&gt;/&lt;class&gt;/, sidecars imported before their FBX
    ///     (one import instead of two, backlog #39), then everything is
    ///     imported and measured.
    ///   verify: -executeMethod Artpipe.Cli.Import --report ABS
    ///     Re-checks every managed asset from scratch: the gate is re-evaluated
    ///     on the files, not read from Unity's import cache.
    ///
    /// Our arguments use a double dash: Unity's use one, so they never collide.
    /// Exit codes: 0 pass / 1 an asset failed / 2 tool error (preflight, bad
    /// arguments, exception).
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

        static readonly string[] ImageExtensions = { ".png", ".tga", ".jpg", ".jpeg", ".psd", ".tif", ".tiff", ".bmp", ".exr", ".hdr" };

        static string KindOf(string path)
        {
            var ext = Path.GetExtension(path).ToLowerInvariant();
            return ext == ".fbx" ? "model" : ImageExtensions.Contains(ext) ? "texture" : null;
        }

        internal static int Run(string[] argv)
        {
            var args = CliArgs.Parse(argv);
            if (args.Error != null)
            {
                Debug.LogError("artpipe: " + args.Error);
                return ExitCode.ToolError;
            }

            var report = new ImportReport { Mode = args.Files.Count > 0 ? "import" : "verify" };
            report.Preflight.AddRange(Preflight.Run());
            if (report.Status == "error")
            {
                report.ToolError = "Unity project preflight failed: see preflight[].";
                return Finish(args, report);
            }

            var conventions = ConventionsLock.Load();
            ConventionsDependency.Register();  // the rules may have changed since the Editor started

            if (report.Mode == "import")
            {
                var problem = ImportFiles(args, conventions, report);
                if (problem != null) report.ToolError = problem;
            }
            else
            {
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                VerifyAll(conventions, report);
            }
            return Finish(args, report);
        }

        static int Finish(CliArgs args, ImportReport report)
        {
            WriteReport(args.ReportPath, report.ToJson());
            Debug.Log($"artpipe: {report.Status.ToUpperInvariant()} {args.ReportPath}");
            return report.ExitCode;
        }

        /// <summary>Returns a tool-error message, or null.</summary>
        static string ImportFiles(CliArgs args, ConventionsLock c, ImportReport report)
        {
            if (args.AssetClass == null) return "--class is required with --file";
            if (!c.Classes.ContainsKey(args.AssetClass))
                return $"unknown --class '{args.AssetClass}', expected one of: {string.Join(", ", c.Classes.Keys.OrderBy(k => k, StringComparer.Ordinal))}";
            var unknown = args.Files.Where(f => KindOf(f) == null).ToList();
            if (unknown.Count > 0) return "unsupported input file(s): " + string.Join(", ", unknown.Select(RepoPath));
            var missing = args.Files.Where(f => !File.Exists(f)).ToList();
            if (missing.Count > 0) return "input file(s) not found: " + string.Join(", ", missing.Select(RepoPath));

            var folder = $"{c.UnityImport.Root}/{args.AssetClass}";
            Directory.CreateDirectory(folder);

            var accepted = new List<(string src, string dest, AssetReport entry)>();
            var created = new List<string>();  // files new in this run (deterministic GUIDs)
            foreach (var src in args.Files.Distinct().OrderBy(f => f, StringComparer.Ordinal))
            {
                var dest = $"{folder}/{Path.GetFileName(src)}";
                var entry = new AssetReport
                {
                    Path = dest, Kind = KindOf(src), AssetClass = args.AssetClass,
                    SourcePath = RepoPath(src), SourceSha256 = ConventionsLock.Sha256(src),
                };
                report.Assets.Add(entry);
                if (entry.Kind == "model")
                {
                    // The inbox gate: judged on the source, before it can enter the project.
                    var gate = ImportGate.Evaluate(src, c, args.AssetClass);
                    if (gate.Any(r => r.Blocking))
                    {
                        // Offenders name the project path the asset would have had.
                        foreach (var r in gate) r.Offenders = r.Offenders.Select(_ => dest).ToList();
                        entry.Results.AddRange(gate);
                        continue;
                    }
                    var sidecar = ImportGate.SidecarPath(dest);
                    if (!File.Exists(sidecar + ".meta")) created.Add(sidecar);
                    CopyIfChanged(ImportGate.SidecarPath(src), sidecar);
                }
                accepted.Add((src, dest, entry));
            }

            // Sidecars first: an FBX imported in the same refresh as its sidecar
            // would be imported twice (the sidecar is a declared dependency).
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            created.AddRange(accepted.Where(a => !File.Exists(a.dest + ".meta")).Select(a => a.dest));
            foreach (var (src, dest, _) in accepted) CopyIfChanged(src, dest);
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            if (created.Count(SetDeterministicGuid) > 0)
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);

            // Shared materials from textures imported with their model (backlog #49).
            // The new .mat gets its deterministic GUID before the FBX is reimported
            // against it: the FBX's remap stores the material's GUID.
            var textures = accepted.Where(a => a.entry.Kind == "texture").Select(a => a.dest).ToList();
            var newMaterials = new Dictionary<string, List<string>>();  // model -> materials created for it
            foreach (var (_, dest, _) in accepted.Where(a => a.entry.Kind == "model"))
            {
                var made = SharedMaterials.Create(SharedMaterials.Plan(dest, textures, c));
                if (made.Count > 0) newMaterials[dest] = made;
            }
            if (newMaterials.Count > 0)
            {
                foreach (var m in newMaterials.Values.SelectMany(m => m)) SetDeterministicGuid(m);
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                foreach (var model in newMaterials.Keys.OrderBy(k => k, StringComparer.Ordinal))
                    AssetDatabase.ImportAsset(model, ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
            }

            foreach (var (_, dest, entry) in accepted)
            {
                entry.Imported = true;
                entry.Results.AddRange(Checks(dest, entry.Kind, c));
                if (entry.Kind == "model") entry.Materials = SharedMaterialsOf(dest, c);
            }

            // A clean model gets its Prefab Variant: the asset game code references.
            var newPrefabs = new List<string>();
            foreach (var (_, dest, entry) in accepted.Where(a => a.entry.Kind == "model" && a.entry.Passed))
            {
                entry.Prefab = PrefabPath(dest);
                if (EnsurePrefabVariant(dest)) newPrefabs.Add(entry.Prefab);
            }
            if (newPrefabs.Count(SetDeterministicGuid) > 0)
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            foreach (var (_, dest, entry) in accepted.Where(a => a.entry.Kind == "model" && a.entry.Passed))
                entry.Results.Add(PrefabCheck(dest));
            return null;
        }

        static void VerifyAll(ConventionsLock c, ImportReport report)
        {
            var root = c.UnityImport.Root;
            if (!Directory.Exists(root)) return;
            foreach (var file in Directory.GetFiles(root, "*", SearchOption.AllDirectories)
                         .Select(f => f.Replace('\\', '/')).OrderBy(f => f, StringComparer.Ordinal))
            {
                var kind = KindOf(file);
                if (kind == null) continue;
                var folderClass = c.UnityImport.FolderClass(file);
                var entry = new AssetReport
                {
                    Path = file, Kind = kind, Imported = true,
                    AssetClass = c.Classes.ContainsKey(folderClass) ? folderClass : null,
                    Results = Checks(file, kind, c),
                };
                if (kind == "model") entry.Materials = SharedMaterialsOf(file, c);
                if (kind == "model" && !entry.Results.Any(r => r.Id.StartsWith("gate.") && r.Blocking))
                {
                    entry.Results.Add(PrefabCheck(file));
                    if (File.Exists(PrefabPath(file))) entry.Prefab = PrefabPath(file);
                }
                report.Assets.Add(entry);
            }
        }

        /// <summary>Every check for one asset already in the project. Uncached: re-evaluated from the files.</summary>
        static List<CheckResult> Checks(string assetPath, string kind, ConventionsLock c)
        {
            if (kind == "texture")
            {
                var ti = (TextureImporter)AssetImporter.GetAtPath(assetPath);
                var results = TextureRulesUnity.SourceChecks(ti, c, assetPath);
                results.Add(TextureRulesUnity.PlatformFormat(AssetDatabase.LoadAssetAtPath<Texture2D>(assetPath), c, assetPath));
                return results;
            }
            var folderClass = c.UnityImport.FolderClass(assetPath);
            var gate = ImportGate.Evaluate(assetPath, c, folderClass);
            if (gate.Any(r => r.Blocking)) return gate;
            var go = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
            if (go == null)
            {
                gate.Add(new CheckResult
                {
                    Id = "unity.imported", Passed = false, Severity = Severity.Error, Fixable = false,
                    Message = "Unity produced no model from this FBX (see the import log).",
                    Offenders = new List<string> { assetPath },
                });
                return gate;
            }
            gate.AddRange(ModelChecks.Run(go, c.Class(folderClass), assetPath));
            return gate;
        }

        /// <summary>The shared material files this model's FBX materials map to (those that exist).</summary>
        static List<string> SharedMaterialsOf(string modelPath, ConventionsLock c) =>
            ModelRules.FbxMaterialNames(modelPath).Select(n => ModelRules.SharedMaterialPath(c.UnityImport, n))
                .Where(File.Exists).ToList();

        /// <summary>The model's Prefab Variant exists, loads, and really derives from this model.</summary>
        public static CheckResult PrefabCheck(string modelPath)
        {
            var path = PrefabPath(modelPath);
            var r = new CheckResult { Id = "unity.prefab", Severity = Severity.Warning, Fixable = true };
            if (!File.Exists(path))
            {
                r.Message = $"No {Path.GetFileName(path)}: game code should reference a Prefab Variant, not the FBX. `artpipe unity import` creates it.";
                r.Offenders.Add(modelPath);
                return r;
            }
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(modelPath);
            bool variant = prefab != null && PrefabUtility.GetPrefabAssetType(prefab) == PrefabAssetType.Variant
                           && PrefabUtility.GetCorrespondingObjectFromSource(prefab) == model;
            r.Passed = variant;
            r.Severity = variant ? Severity.Warning : Severity.Error;
            r.Fixable = false;
            r.Message = variant ? $"{path} is a Prefab Variant of the model."
                                : $"{path} exists but is not a Prefab Variant of {Path.GetFileName(modelPath)}.";
            if (!variant) r.Offenders.Add(path);
            return r;
        }

        public static string PrefabPath(string modelPath) => Path.ChangeExtension(modelPath, ".prefab").Replace('\\', '/');

        /// <summary>
        /// Create &lt;Name&gt;.prefab as a Prefab Variant of the imported model, once.
        /// A variant follows every FBX reimport, and leaves room for gameplay
        /// components (colliders, scripts) without touching the art. An existing
        /// prefab is never overwritten: someone may have edited it.
        /// </summary>
        public static bool EnsurePrefabVariant(string modelPath)
        {
            var path = PrefabPath(modelPath);
            if (File.Exists(path)) return false;
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(modelPath);
            var instance = (GameObject)PrefabUtility.InstantiatePrefab(model);
            try { PrefabUtility.SaveAsPrefabAsset(instance, path); }
            finally { UnityEngine.Object.DestroyImmediate(instance); }
            PinPrefabInstanceId(path);
            return true;
        }

        static readonly System.Text.RegularExpressions.Regex ObjectHeader =
            new System.Text.RegularExpressions.Regex("^--- !u!(\\d+) &(-?\\d+)", System.Text.RegularExpressions.RegexOptions.Multiline);

        /// <summary>
        /// Probed: a new Prefab Variant gets a random local file ID for its
        /// PrefabInstance on every creation, so two machines write two different
        /// files. In a fresh variant that ID is defined once and referenced
        /// nowhere, so it is replaced with one derived from the path. Any other
        /// layout is left untouched rather than guessed at.
        /// </summary>
        static void PinPrefabInstanceId(string path)
        {
            var text = File.ReadAllText(path);
            var headers = ObjectHeader.Matches(text);
            if (headers.Count != 1 || headers[0].Groups[1].Value != "1001") return;
            var id = headers[0].Groups[2].Value;
            if (text.Split(new[] { id }, StringSplitOptions.None).Length != 2) return;  // referenced elsewhere
            long pinned;
            using (var md5 = MD5.Create())
                pinned = BitConverter.ToInt64(md5.ComputeHash(Encoding.UTF8.GetBytes("artpipe-prefab:" + path)), 0) & long.MaxValue;
            if (pinned == 0) pinned = 1;
            File.WriteAllText(path, text.Replace("&" + id, "&" + pinned), new UTF8Encoding(false));
        }

        static void CopyIfChanged(string src, string dest)
        {
            if (File.Exists(dest) && ConventionsLock.Sha256(dest) == ConventionsLock.Sha256(src)) return;  // idempotent
            File.Copy(src, dest, true);
        }

        /// <summary>
        /// A new file normally gets a random GUID, so the same asset imported on
        /// two machines gets two GUIDs and every reference to it differs. After
        /// Unity has written the full .meta, swap its GUID for one derived from
        /// the asset path, then refresh again. Only for files new in this run:
        /// changing an existing asset's GUID would break every reference to it.
        ///
        /// Probed: pre-writing a minimal .meta (GUID only) keeps the GUID but
        /// Unity then never writes the importer settings, builds the importer
        /// from zeroed values, and a plain PNG imported as a Cubemap.
        /// </summary>
        public static bool SetDeterministicGuid(string assetPath)
        {
            var meta = assetPath + ".meta";
            var text = File.ReadAllText(meta);
            var swapped = System.Text.RegularExpressions.Regex.Replace(
                text, "^guid: [0-9a-f]{32}$", "guid: " + DeterministicGuid(assetPath),
                System.Text.RegularExpressions.RegexOptions.Multiline);
            if (swapped == text) return false;
            File.WriteAllText(meta, swapped, new UTF8Encoding(false));
            return true;
        }

        public static string DeterministicGuid(string assetPath)
        {
            using (var md5 = MD5.Create())
                return string.Concat(md5.ComputeHash(Encoding.UTF8.GetBytes("artpipe:" + assetPath)).Select(b => b.ToString("x2")));
        }

        /// <summary>Repo-relative POSIX path when inside the repo, like the Maya reports.</summary>
        public static string RepoPath(string path)
        {
            var full = Path.GetFullPath(path).Replace('\\', '/');
            var repo = Path.GetFullPath(Path.Combine(Path.GetDirectoryName(Application.dataPath), "..")).Replace('\\', '/').TrimEnd('/') + "/";
            return full.StartsWith(repo, StringComparison.OrdinalIgnoreCase) ? full.Substring(repo.Length) : full;
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
        public string AssetClass;
        public List<string> Files = new List<string>();
        public string Error;

        public static CliArgs Parse(IList<string> argv)
        {
            var a = new CliArgs();
            for (int i = 0; i < argv.Count; i++)
            {
                string arg = argv[i];
                if (!arg.StartsWith("--", StringComparison.Ordinal)) continue;  // Unity's own
                if (arg != "--report" && arg != "--class" && arg != "--file")
                {
                    a.Error = $"unknown argument {arg}";
                    return a;
                }
                if (i + 1 >= argv.Count || argv[i + 1].StartsWith("-", StringComparison.Ordinal))
                {
                    a.Error = $"{arg} needs a value";
                    return a;
                }
                var value = argv[++i];
                if (arg == "--report") a.ReportPath = value;
                else if (arg == "--class") a.AssetClass = value;
                else a.Files.Add(value);
            }
            // Unity's working directory is the PROJECT folder, not the caller's,
            // so a relative path would silently land inside unity/. Refuse it.
            if (a.ReportPath == null) a.Error = "--report PATH is required";
            else if (!Path.IsPathFullyQualified(a.ReportPath))
                a.Error = $"--report must be an absolute path, got {a.ReportPath}";
            else if (a.Files.Any(f => !Path.IsPathFullyQualified(f)))
                a.Error = $"--file must be an absolute path, got {a.Files.First(f => !Path.IsPathFullyQualified(f))}";
            return a;
        }
    }
}
