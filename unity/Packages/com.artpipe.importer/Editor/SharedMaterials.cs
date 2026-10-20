using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace Artpipe
{
    /// <summary>
    /// Creates the shared material a model's FBX material is remapped to
    /// (backlog #49): FBX material M_X -> &lt;materials&gt;/M_X.mat, a URP Lit
    /// material with T_X_&lt;type&gt; textures in the slots their map type means
    /// (texture_rules.yaml channels: rgb base_color -> _BaseMap, rgb normal ->
    /// _BumpMap). Only from textures imported in the same run as the model, and
    /// only with a base color map: anything else is left to a human, with
    /// `unity.materials` still warning.
    ///
    /// An existing material is never touched (someone may have tuned it), so
    /// a re-import changes nothing. A new material gets a deterministic GUID
    /// before the FBX picks it up (see Cli.ImportFiles for the order).
    /// </summary>
    public static class SharedMaterials
    {
        public const string Shader = "Universal Render Pipeline/Lit";

        // URP Lit's texture property per channel meaning. Shader knowledge, not
        // a rule: which file is which map comes from the conventions.
        static readonly Dictionary<string, string> Slots = new Dictionary<string, string>
        {
            ["base_color"] = "_BaseMap",
            ["normal"] = "_BumpMap",
        };

        /// <summary>The material files (asset paths) that would be created for this FBX, with their textures by slot.</summary>
        public static List<(string path, SortedDictionary<string, string> maps)> Plan(
            string fbxPath, IEnumerable<string> textures, ConventionsLock c)
        {
            var plans = new List<(string, SortedDictionary<string, string>)>();
            var available = textures.ToList();
            foreach (var name in ModelRules.FbxMaterialNames(fbxPath))
            {
                var path = ModelRules.SharedMaterialPath(c.UnityImport, name);
                if (File.Exists(path) || !name.StartsWith("M_", StringComparison.Ordinal)) continue;
                var stem = name.Substring(2);
                var maps = new SortedDictionary<string, string>(StringComparer.Ordinal);
                foreach (var t in available.OrderBy(t => t, StringComparer.Ordinal))
                {
                    var type = TextureRulesUnity.MapType(t, c.Textures);
                    if (type == null || Path.GetFileNameWithoutExtension(t) != $"T_{stem}_{type}") continue;
                    if (c.Textures.Types[type].Channels.TryGetValue("rgb", out var meaning)
                        && Slots.TryGetValue(meaning, out var slot) && !maps.ContainsKey(slot))
                        maps[slot] = t;
                }
                if (maps.ContainsKey("_BaseMap")) plans.Add((path, maps));
            }
            return plans;
        }

        /// <summary>Create the planned materials. Returns their asset paths (sorted).</summary>
        public static List<string> Create(IEnumerable<(string path, SortedDictionary<string, string> maps)> plans)
        {
            var created = new List<string>();
            var shader = UnityEngine.Shader.Find(Shader);
            foreach (var (path, maps) in plans.OrderBy(p => p.path, StringComparer.Ordinal))
            {
                if (shader == null) throw new ConventionsException($"shader '{Shader}' not found: is URP installed?");
                EnsureFolder(Path.GetDirectoryName(path).Replace('\\', '/'));
                var mat = new Material(shader) { name = Path.GetFileNameWithoutExtension(path) };
                foreach (var kv in maps)
                    mat.SetTexture(kv.Key, AssetDatabase.LoadAssetAtPath<Texture2D>(kv.Value));
                if (maps.ContainsKey("_BumpMap")) mat.EnableKeyword("_NORMALMAP");
                AssetDatabase.CreateAsset(mat, path);
                created.Add(path);
            }
            return created;
        }

        static void EnsureFolder(string folder)
        {
            if (AssetDatabase.IsValidFolder(folder)) return;
            var parent = Path.GetDirectoryName(folder).Replace('\\', '/');
            EnsureFolder(parent);
            AssetDatabase.CreateFolder(parent, Path.GetFileName(folder));
        }
    }
}
