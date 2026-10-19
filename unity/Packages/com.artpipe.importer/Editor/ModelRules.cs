using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using UnityEditor;
using UnityEngine;

namespace Artpipe
{
    /// <summary>
    /// Applies conventions/unity_import.yaml to a ModelImporter. Every value
    /// comes from the lock; this class only maps names to Unity enums.
    ///
    /// Probed on 6000.5: settings set in OnPreprocessModel apply to that same
    /// import and are written to the .meta, so applying them on every import
    /// is idempotent (same rules, same .meta bytes).
    /// </summary>
    public static class ModelRules
    {
        public static void Apply(ModelImporter mi, ModelSettings s)
        {
            // Units: the FBX is in cm (checked at export: UnitScaleFactor 1),
            // and the file scale (0.01) converts it to Unity metres. Never
            // fixed by hand with globalScale.
            mi.useFileScale = true;
            mi.globalScale = 1f;

            mi.meshCompression = Pick(s.MeshCompression, "mesh_compression", new Dictionary<string, ModelImporterMeshCompression>
            {
                ["off"] = ModelImporterMeshCompression.Off, ["low"] = ModelImporterMeshCompression.Low,
                ["medium"] = ModelImporterMeshCompression.Medium, ["high"] = ModelImporterMeshCompression.High,
            });
            mi.isReadable = s.ReadWrite;
            mi.optimizeMeshPolygons = s.OptimizeMesh;
            mi.optimizeMeshVertices = s.OptimizeMesh;
            mi.weldVertices = true;
            mi.indexFormat = ModelImporterIndexFormat.Auto;  // 16-bit indices below 65k vertices
            mi.importNormals = Pick(s.Normals, "normals", new Dictionary<string, ModelImporterNormals>
            {
                ["import"] = ModelImporterNormals.Import, ["calculate"] = ModelImporterNormals.Calculate,
            });
            mi.importTangents = Pick(s.Tangents, "tangents", new Dictionary<string, ModelImporterTangents>
            {
                ["import"] = ModelImporterTangents.Import, ["calculate_mikk"] = ModelImporterTangents.CalculateMikk,
                ["none"] = ModelImporterTangents.None,
            });
            mi.generateSecondaryUV = s.LightmapUvs;
            mi.importBlendShapes = s.BlendShapes;
            mi.importCameras = s.Cameras;
            mi.importLights = s.Lights;
            mi.importVisibility = s.Visibility;
            mi.addCollider = false;  // colliders are authored by design, not generated per art asset

            mi.importAnimation = s.Animation;
            mi.animationType = Pick(s.Rig, "rig", new Dictionary<string, ModelImporterAnimationType>
            {
                ["none"] = ModelImporterAnimationType.None, ["generic"] = ModelImporterAnimationType.Generic,
            });
            if (mi.animationType != ModelImporterAnimationType.None)
            {
                mi.skinWeights = ModelImporterSkinWeights.Custom;
                mi.maxBonesPerVertex = s.SkinWeights;
            }

            mi.materialImportMode = ModelImporterMaterialImportMode.ImportViaMaterialDescription;
            mi.materialLocation = ModelImporterMaterialLocation.InPrefab;
        }

        static T Pick<T>(string value, string setting, Dictionary<string, T> map)
        {
            if (value != null && map.TryGetValue(value, out var v)) return v;
            throw new ConventionsException($"unity_import {setting}: unsupported value '{value}'");
        }

        static readonly Regex FbxMaterial = new Regex("^\\tMaterial: \\d+, \"Material::([^\"]+)\"", RegexOptions.Multiline);

        /// <summary>
        /// Material names in the FBX. Read from the file itself: the gate already
        /// guarantees an artpipe-exported ASCII FBX, and preprocessing runs before
        /// Unity has parsed it.
        /// </summary>
        public static List<string> FbxMaterialNames(string fbxPath) =>
            FbxMaterial.Matches(File.ReadAllText(fbxPath)).Cast<Match>().Select(m => m.Groups[1].Value)
                .Distinct().OrderBy(n => n, StringComparer.Ordinal).ToList();

        public static string SharedMaterialPath(UnityImportRules rules, string materialName) =>
            $"{rules.Materials}/{materialName}.mat";

        /// <summary>
        /// Remap each FBX material to the shared material of the same name when it
        /// exists, and drop remaps whose material is gone. Returns the paths the
        /// result depends on (existing or not), to declare as import dependencies.
        /// </summary>
        public static List<string> RemapMaterials(ModelImporter mi, UnityImportRules rules, string fbxPath)
        {
            var dependencies = new List<string>();
            foreach (var name in FbxMaterialNames(fbxPath))
            {
                var path = SharedMaterialPath(rules, name);
                dependencies.Add(path);
                var id = new AssetImporter.SourceAssetIdentifier(typeof(Material), name);
                var shared = AssetDatabase.LoadAssetAtPath<Material>(path);
                if (shared != null) mi.AddRemap(id, shared);
                else mi.RemoveRemap(id);
            }
            return dependencies;
        }

        /// <summary>Set LOD switch heights from the conventions on Unity's auto-built LODGroup.</summary>
        public static void ApplyLods(GameObject root, LodRulesUnity rules)
        {
            var group = root.GetComponent<LODGroup>();
            if (group == null) return;
            var lods = group.GetLODs();
            if (lods.Length - 1 > rules.Transitions.Count)
                throw new ConventionsException(
                    $"{root.name} has {lods.Length} LODs but unity_import.lods.transitions only has {rules.Transitions.Count} entries");
            for (int i = 0; i < lods.Length; i++)
                lods[i].screenRelativeTransitionHeight = (float)(i < lods.Length - 1 ? rules.Transitions[i] : rules.CullBelow);
            group.SetLODs(lods);
            group.RecalculateBounds();
        }
    }
}
