using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using UnityEditor;
using UnityEngine;

namespace Artpipe
{
    /// <summary>
    /// Registers the conventions as a custom import dependency, so every
    /// artpipe-managed asset reimports when the rules change.
    ///
    /// Probed on 6000.5: an import reruns exactly when a declared dependency
    /// changes, and Unity reuses a cached result (replaying its stored log)
    /// when all dependencies match an earlier import. So every input a verdict
    /// reads must be declared, or the cache serves a stale verdict.
    /// </summary>
    [InitializeOnLoad]
    public static class ConventionsDependency
    {
        public const string Name = "artpipe/conventions";

        static ConventionsDependency() => Register();

        /// <summary>Hash of every file in conventions/ (lock AND sources: a stale lock changes the verdict too).</summary>
        public static Hash128 Register()
        {
            var hash = Compute(ConventionsLock.DefaultDirectory());
            AssetDatabase.RegisterCustomDependency(Name, hash);
            return hash;
        }

        public static Hash128 Compute(string directory)
        {
            var sb = new StringBuilder();
            if (Directory.Exists(directory))
                foreach (var f in Directory.GetFiles(directory).OrderBy(p => Path.GetFileName(p), System.StringComparer.Ordinal))
                    sb.Append(Path.GetFileName(f)).Append(':').Append(ConventionsLock.Sha256(f)).Append('\n');
            else
                sb.Append("missing");
            return Hash128.Compute(sb.ToString());
        }
    }

    /// <summary>
    /// The import gate as an AssetPostprocessor, for every .fbx under the
    /// managed root (conventions: unity_import.root).
    ///
    /// Probed: an AssetPostprocessor can't cancel an import, and
    /// context.LogImportError only flags it (the model is still created). So a
    /// refused FBX is made inert: its renderers and LOD groups are stripped,
    /// so it can't ship looking normal. The error stays on the asset and in
    /// the batchmode report. `artpipe unity import` (step 7) re-checks every
    /// managed FBX without any cache, so a stale verdict can't hide a problem.
    /// </summary>
    public class ModelGatePostprocessor : AssetPostprocessor
    {
        /// <summary>Bump when the gate's logic changes: Unity reimports existing models only when this changes.</summary>
        public const uint Version = 1;
        public override uint GetVersion() => Version;

        // Run before any other model postprocessor: nothing should configure
        // an asset the gate is about to refuse.
        public override int GetPostprocessOrder() => -1000;

        List<CheckResult> _verdict;

        void OnPreprocessModel()
        {
            if (!assetPath.EndsWith(".fbx", System.StringComparison.OrdinalIgnoreCase)) return;
            ConventionsLock conventions;
            try { conventions = ConventionsLock.Load(); }
            catch (ConventionsException) { return; }  // preflight (env.conventions) reports this as a tool error
            if (!conventions.UnityImport.Manages(assetPath)) return;

            context.DependsOnCustomDependency(ConventionsDependency.Name);
            context.DependsOnSourceAsset(ImportGate.SidecarPath(assetPath));

            _verdict = ImportGate.Evaluate(assetPath, conventions);
            foreach (var r in _verdict.Where(r => r.Blocking))
                context.LogImportError($"artpipe gate refused {assetPath}: {r.Id}: {r.Message}");
        }

        void OnPostprocessModel(GameObject root)
        {
            if (_verdict == null || !_verdict.Any(r => r.Blocking)) return;
            foreach (var c in root.GetComponentsInChildren<LODGroup>(true)) Object.DestroyImmediate(c);
            foreach (var c in root.GetComponentsInChildren<Renderer>(true)) Object.DestroyImmediate(c);
            foreach (var c in root.GetComponentsInChildren<MeshFilter>(true)) Object.DestroyImmediate(c);
        }

        public static bool Refused(GameObject root) =>
            root != null && root.GetComponentsInChildren<Renderer>(true).Length == 0;
    }
}
