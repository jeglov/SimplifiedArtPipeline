using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace Artpipe
{
    /// <summary>
    /// Every .fbx under the managed root (unity_import.root) goes through:
    ///   1. the import gate (ImportGate): sidecar report, hash, settings, class folder;
    ///   2. ModelImporter settings for its class (ModelRules, from the lock);
    ///   3. shared-material remaps by name;
    ///   4. after import: LOD switch heights, then ModelChecks on what the GPU gets.
    ///
    /// Probed: an AssetPostprocessor can't cancel an import (LogImportError only
    /// flags it), so a refused FBX is also made inert: renderers, filters and LOD
    /// groups stripped, so it can't ship looking normal. `artpipe unity import`
    /// re-checks every managed FBX without the import cache.
    ///
    /// Also probed: Unity reruns an import only when a DECLARED dependency
    /// changes, otherwise it replays the cached result and its log. So every
    /// input is declared: the sidecar, the shared material files (even missing
    /// ones), the conventions (custom dependency) and GetVersion().
    /// </summary>
    public class ModelPostprocessor : AssetPostprocessor
    {
        /// <summary>Bump when this logic changes: Unity reimports existing models only when this changes.</summary>
        public const uint Version = 2;
        public override uint GetVersion() => Version;

        // Run before other model postprocessors: they should see our settings.
        public override int GetPostprocessOrder() => -1000;

        ConventionsLock _conventions;
        string _class;
        List<CheckResult> _gate;

        bool Refused => _gate != null && _gate.Any(r => r.Blocking);

        void OnPreprocessModel()
        {
            if (!assetPath.EndsWith(".fbx", System.StringComparison.OrdinalIgnoreCase)) return;
            try { _conventions = ConventionsLock.Load(); }
            catch (ConventionsException) { return; }  // preflight (env.conventions) reports this as a tool error
            var rules = _conventions.UnityImport;
            if (!rules.Manages(assetPath)) { _conventions = null; return; }

            context.DependsOnCustomDependency(ConventionsDependency.Name);
            context.DependsOnSourceAsset(ImportGate.SidecarPath(assetPath));

            var folderClass = rules.FolderClass(assetPath);
            _gate = ImportGate.Evaluate(assetPath, _conventions, folderClass);
            foreach (var r in _gate.Where(r => r.Blocking))
                context.LogImportError($"artpipe gate refused {assetPath}: {r.Id}: {r.Message}");
            if (Refused) return;

            _class = folderClass;  // the gate proved folder == report class
            var mi = (ModelImporter)assetImporter;
            ModelRules.Apply(mi, rules.Model[_class]);
            foreach (var dependency in ModelRules.RemapMaterials(mi, rules, assetPath))
                context.DependsOnSourceAsset(dependency);
        }

        void OnPostprocessModel(GameObject root)
        {
            if (_conventions == null) return;
            if (Refused)
            {
                MakeInert(root);
                return;
            }
            ModelRules.ApplyLods(root, _conventions.UnityImport.Lods);
            foreach (var r in ModelChecks.Run(root, _conventions.Class(_class), assetPath).Where(r => !r.Passed))
            {
                var line = $"artpipe {assetPath}: {r.Id}: {r.Message}";
                if (r.Blocking) context.LogImportError(line);
                else context.LogImportWarning(line);
            }
        }

        static void MakeInert(GameObject root)
        {
            foreach (var c in root.GetComponentsInChildren<LODGroup>(true)) Object.DestroyImmediate(c);
            foreach (var c in root.GetComponentsInChildren<Renderer>(true)) Object.DestroyImmediate(c);
            foreach (var c in root.GetComponentsInChildren<MeshFilter>(true)) Object.DestroyImmediate(c);
        }

        /// <summary>True when an imported model was refused by the gate (no renderers left).</summary>
        public static bool IsInert(GameObject root) =>
            root != null && root.GetComponentsInChildren<Renderer>(true).Length == 0;
    }
}
