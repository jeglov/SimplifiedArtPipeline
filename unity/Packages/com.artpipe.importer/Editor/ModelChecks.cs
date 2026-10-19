using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using UnityEditor;
using UnityEngine;

namespace Artpipe
{
    /// <summary>
    /// Measures what the GPU really gets after Unity's import, against the
    /// class budgets (backlog #11). Maya counts a vertex once; Unity splits it
    /// at every UV seam and hard edge, so the vertex count only exists here.
    /// Probed: SM_LodsGood passes Maya (LOD1 = 24% of LOD0's triangles) but
    /// its LOD1 keeps 87% of LOD0's VERTICES (built with every edge hard), so
    /// the LOD saves almost no vertex work.
    ///
    /// Same result shape and ids style as Phase 1. Works on the imported
    /// GameObject, so the postprocessor and the batchmode CLI run the exact
    /// same measurement.
    /// </summary>
    public static class ModelChecks
    {
        static readonly Regex LodSuffix = new Regex("_LOD([0-9]+)$");

        public static List<CheckResult> Run(GameObject root, AssetClass cls, string assetPath)
        {
            var renderers = root.GetComponentsInChildren<Renderer>(true)
                .Where(r => MeshOf(r) != null).OrderBy(r => r.name, StringComparer.Ordinal).ToList();
            var byLod = renderers.GroupBy(LodLevel).OrderBy(g => g.Key).ToDictionary(g => g.Key, g => g.ToList());
            return new List<CheckResult>
            {
                VertexBudget(renderers, byLod, cls),
                LodVertexRatio(byLod, cls),
                Submeshes(renderers, cls),
                LodGroupMatchesNames(root, byLod),
                SharedMaterials(renderers, assetPath),
            };
        }

        public static Mesh MeshOf(Renderer r) =>
            r is SkinnedMeshRenderer s ? s.sharedMesh : r.GetComponent<MeshFilter>()?.sharedMesh;

        /// <summary>LOD level from the name suffix; -1 when the asset has no LODs.</summary>
        static int LodLevel(Renderer r)
        {
            var m = LodSuffix.Match(r.name);
            return m.Success ? int.Parse(m.Groups[1].Value) : -1;
        }

        static int Verts(IEnumerable<Renderer> rs) => rs.Sum(r => MeshOf(r).vertexCount);

        static CheckResult VertexBudget(List<Renderer> all, Dictionary<int, List<Renderer>> byLod, AssetClass cls)
        {
            // The budget is for what's drawn up close: LOD0, or everything when there are no LODs.
            var lod0 = byLod.ContainsKey(0) ? byLod[0] : all;
            int verts = Verts(lod0);
            bool ok = verts <= cls.VertsMax;
            var r = Result("unity.vertex_budget", ok, Severity.Error,
                ok ? $"{verts} GPU vertices (budget {cls.VertsMax})."
                   : $"{verts} GPU vertices, {verts - cls.VertsMax} over the {cls.VertsMax} budget. " +
                     "Each UV seam and hard edge duplicates vertices: merge UV shells or soften edges that don't need to be hard.",
                ("verts", verts), ("verts_max", cls.VertsMax));
            if (!ok) r.Offenders.AddRange(lod0.Select(x => x.name));
            return r;
        }

        static CheckResult LodVertexRatio(Dictionary<int, List<Renderer>> byLod, AssetClass cls)
        {
            if (!byLod.ContainsKey(0) || byLod.Count < 2)
                return Result("unity.lod_vertex_ratio", true, Severity.Error, "No LODs to compare.", ("lods", byLod.Count));
            int lod0 = Verts(byLod[0]);
            var r = Result("unity.lod_vertex_ratio", true, Severity.Error, "", ("lod0_verts", lod0));
            var problems = new List<string>();
            foreach (var kv in byLod.Where(kv => kv.Key > 0))
            {
                int level = kv.Key, verts = Verts(kv.Value);
                if (level >= cls.Lods.MaxRatio.Count) { problems.Add($"LOD{level} has no ratio in the conventions"); continue; }
                double ratio = cls.Lods.MaxRatio[level];
                // Same rule as Maya's tri check: max = LOD0 * ratio * (1 + tolerance), truncated.
                int max = (int)(lod0 * ratio * (1.0 + cls.Lods.Tolerance));
                r.Metrics[$"lod{level}_verts"] = verts;
                r.Metrics[$"lod{level}_verts_max"] = max;
                if (verts > max)
                {
                    problems.Add($"LOD{level} has {verts} vertices, max {max} ({ratio:0.###} of LOD0's {lod0} +{cls.Lods.Tolerance:P0})");
                    r.Offenders.AddRange(kv.Value.Select(x => x.name));
                }
            }
            r.Passed = problems.Count == 0;
            r.Message = r.Passed ? "Every LOD reduces the vertex count as required."
                : string.Join("; ", problems) + ". Check the LOD for hard edges or extra UV seams: they split vertices.";
            return r;
        }

        static CheckResult Submeshes(List<Renderer> renderers, AssetClass cls)
        {
            // One submesh = one draw call (one material slot) per renderer.
            var over = renderers.Where(r => MeshOf(r).subMeshCount > cls.MaxMaterials).ToList();
            int most = renderers.Count == 0 ? 0 : renderers.Max(r => MeshOf(r).subMeshCount);
            var r = Result("unity.submeshes", over.Count == 0, Severity.Error,
                over.Count == 0 ? $"At most {most} submesh(es) per mesh (max {cls.MaxMaterials})."
                    : $"{over.Count} mesh(es) have more than {cls.MaxMaterials} submeshes: each one is an extra draw call.",
                ("max_submeshes", most), ("max_materials", cls.MaxMaterials));
            r.Offenders.AddRange(over.Select(x => x.name));
            return r;
        }

        static CheckResult LodGroupMatchesNames(GameObject root, Dictionary<int, List<Renderer>> byLod)
        {
            var group = root.GetComponent<LODGroup>();
            int levels = byLod.Keys.Count(k => k >= 0);
            if (levels <= 1)
            {
                bool ok = group == null;
                return Result("unity.lod_group", ok, Severity.Error,
                    ok ? "No LODs, no LODGroup." : "A LODGroup exists but the meshes have no _LODn levels.", ("levels", levels));
            }
            var lods = group != null ? group.GetLODs() : new LOD[0];
            bool match = lods.Length == levels && Enumerable.Range(0, levels).All(i =>
                lods[i].renderers.Length > 0 && lods[i].renderers.All(x => x != null && LodLevel(x) == i));
            return Result("unity.lod_group", match, Severity.Error,
                match ? $"LODGroup has {levels} levels matching the _LODn names."
                      : $"Expected a LODGroup with {levels} levels matching the _LODn names, found {lods.Length}.",
                ("levels", levels), ("lod_group_levels", lods.Length));
        }

        static CheckResult SharedMaterials(List<Renderer> renderers, string assetPath)
        {
            // Embedded (per-FBX) materials work, but each asset then has its own
            // material: more SRP Batcher variants, no atlas sharing.
            var embedded = renderers.SelectMany(r => r.sharedMaterials).Where(m => m != null)
                .Where(m => AssetDatabase.GetAssetPath(m) == assetPath || string.IsNullOrEmpty(AssetDatabase.GetAssetPath(m)))
                .Select(m => m.name).Distinct().OrderBy(n => n, StringComparer.Ordinal).ToList();
            var r = Result("unity.materials", embedded.Count == 0, Severity.Warning,
                embedded.Count == 0 ? "Every material is a shared project material."
                    : $"{embedded.Count} material(s) embedded in the FBX. Add {string.Join(", ", embedded.Select(n => n + ".mat"))} to the shared materials folder to remap them.",
                ("embedded", embedded.Count));
            r.Offenders.AddRange(embedded);
            return r;
        }

        static CheckResult Result(string id, bool passed, string severity, string message,
                                  params (string key, object value)[] metrics)
        {
            var r = new CheckResult { Id = id, Passed = passed, Severity = severity, Message = message, Fixable = false };
            foreach (var (k, v) in metrics) r.Metrics[k] = v;
            return r;
        }
    }
}
