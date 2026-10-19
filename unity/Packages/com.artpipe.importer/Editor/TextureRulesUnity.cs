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
    /// conventions/texture_rules.yaml applied to a TextureImporter (backlog #14),
    /// and the texture checks (backlog #22). The map type comes from the name
    /// suffix (T_Crate_N.png is a normal map), so a wrong name can't be guessed
    /// around: it is reported, and type-specific settings are not applied.
    ///
    /// Probed on 6000.5 with -nographics (no GPU device): the Android target
    /// still gets real ASTC_6x6 / ASTC_4x4 (backlog #33). And a 300x200 (NPOT)
    /// texture came out UNCOMPRESSED (RGBA32, ~4.5x the memory of ASTC 6x6),
    /// which is why texture.platform_format checks the imported result.
    /// </summary>
    public static class TextureRulesUnity
    {
        public static readonly string[] Platforms = { "android", "ios" };

        /// <summary>Unity's platform names for the conventions' platform keys.</summary>
        public static string UnityPlatform(string platform) =>
            platform == "android" ? "Android" : platform == "ios" ? "iPhone"
            : throw new ConventionsException($"unknown texture platform '{platform}'");

        static readonly Regex Suffix = new Regex("_([A-Z]+)\\.[A-Za-z]+$");

        /// <summary>Map type from the file name ("D", "N", ...), or null when the name has none we know.</summary>
        public static string MapType(string assetPath, TextureRules rules)
        {
            var m = Suffix.Match(Path.GetFileName(assetPath));
            return m.Success && rules.Types.ContainsKey(m.Groups[1].Value) ? m.Groups[1].Value : null;
        }

        /// <summary>The class max size, or the default outside class folders (shared atlases).</summary>
        public static int MaxSize(ConventionsLock c, string assetPath)
        {
            var folderClass = c.UnityImport.FolderClass(assetPath);
            return c.Classes.TryGetValue(folderClass, out var cls) ? cls.TextureMaxSize : c.Textures.DefaultMaxSize;
        }

        public static void Apply(TextureImporter ti, ConventionsLock c, string assetPath)
        {
            var rules = c.Textures;
            int maxSize = MaxSize(c, assetPath);
            ti.isReadable = false;
            ti.mipmapEnabled = rules.Mipmaps;
            // Never let Unity silently rescale an NPOT source: the POT rule
            // reports it instead (texture.power_of_two).
            ti.npotScale = TextureImporterNPOTScale.None;
            ti.maxTextureSize = maxSize;
            ti.textureCompression = TextureImporterCompression.Compressed;

            var type = MapType(assetPath, rules);
            if (type == null) return;  // texture.name reports it; no guessing
            var t = rules.Types[type];
            bool normal = t.Channels.ContainsKey("rgb") && t.Channels["rgb"] == "normal";
            ti.textureType = normal ? TextureImporterType.NormalMap : TextureImporterType.Default;
            ti.sRGBTexture = t.ColorSpace == "srgb";
            ti.alphaSource = t.Alpha == "forbidden" ? TextureImporterAlphaSource.None : TextureImporterAlphaSource.FromInput;
            // Alpha used as opacity (base color) is dilated at the edges; alpha
            // used as data (smoothness in MS) must be left alone.
            ti.alphaIsTransparency = t.Channels.TryGetValue("a", out var a) && a == "opacity";

            foreach (var platform in Platforms)
            {
                var ps = ti.GetPlatformTextureSettings(UnityPlatform(platform));
                ps.overridden = true;
                ps.maxTextureSize = maxSize;
                ps.format = Format(t.Formats[platform]);
                ti.SetPlatformTextureSettings(ps);
            }
        }

        public static TextureImporterFormat Format(string name) =>
            Enum.TryParse(name, out TextureImporterFormat f) ? f
            : throw new ConventionsException($"texture format '{name}' is not a Unity TextureImporterFormat");

        static bool IsPot(int n) => n > 0 && (n & (n - 1)) == 0;

        /// <summary>Checks on the source file and importer (valid during and after import).</summary>
        public static List<CheckResult> SourceChecks(TextureImporter ti, ConventionsLock c, string assetPath)
        {
            var rules = c.Textures;
            var results = new List<CheckResult>();
            var name = Path.GetFileName(assetPath);

            bool named = c.Naming["texture_file"].IsMatch(name);
            var type = MapType(assetPath, rules);
            results.Add(Result("texture.name", named && type != null, Severity.Error, assetPath,
                named ? $"Map type {type}." : $"'{name}' doesn't follow T_<Name>[_Variant]_<{string.Join("|", rules.Types.Keys.OrderBy(k => k, StringComparer.Ordinal))}>.<{string.Join("|", rules.SourceFormats)}>, e.g. {c.Naming["texture_file"].Examples[0]}. The suffix sets color space, alpha and compression."));

            var ext = Path.GetExtension(name).TrimStart('.').ToLowerInvariant();
            bool formatOk = rules.SourceFormats.Contains(ext);
            results.Add(Result("texture.source_format", formatOk, Severity.Error, assetPath,
                formatOk ? $"Source is {ext}." : $"Source is .{ext}; use {string.Join(" or ", rules.SourceFormats)} (lossless, flattened).", ("format", ext)));

            ti.GetSourceTextureWidthAndHeight(out int w, out int h);
            bool pot = !rules.PowerOfTwo || (IsPot(w) && IsPot(h));
            results.Add(Result("texture.power_of_two", pot, Severity.Error, assetPath,
                pot ? $"{w}x{h}." : $"{w}x{h} is not a power of two: it gets no full mip chain and may not compress (probed: an NPOT texture shipped uncompressed RGBA32).",
                ("width", w), ("height", h)));

            bool bigEnough = w >= rules.MinSize && h >= rules.MinSize;
            results.Add(Result("texture.min_size", bigEnough, Severity.Error, assetPath,
                bigEnough ? $"At least {rules.MinSize} px." : $"{w}x{h} is below the {rules.MinSize} px minimum.", ("min_size", rules.MinSize)));

            bool square = !rules.SquareRequired || w == h;
            results.Add(Result("texture.square", square, Severity.Error, assetPath,
                square ? "Shape allowed." : $"{w}x{h} must be square."));

            int max = MaxSize(c, assetPath);
            bool fits = w <= max && h <= max;
            results.Add(Result("texture.source_size", fits, Severity.Warning, assetPath,
                fits ? $"Fits the {max} px limit." : $"{w}x{h} source ships at {max} px max: author at the shipped size (smaller repo, no surprise downscale).",
                ("max_size", max)));

            if (type != null)
            {
                var alphaRule = rules.Types[type].Alpha;
                bool has = ti.DoesSourceTextureHaveAlpha();
                bool alphaOk = alphaRule == "optional" || (alphaRule == "required") == has;
                results.Add(Result("texture.alpha", alphaOk, Severity.Error, assetPath,
                    alphaOk ? $"Alpha {alphaRule}, source {(has ? "has" : "has no")} alpha."
                    : alphaRule == "required" ? $"_{type} needs an alpha channel ({string.Join(", ", rules.Types[type].Channels.Select(kv => kv.Key + "=" + kv.Value))})."
                    : $"_{type} must not have an alpha channel: it costs memory and some compressors treat it as data. Export RGB only.",
                    ("has_alpha", has), ("alpha_rule", alphaRule)));
            }
            return results;
        }

        /// <summary>The GPU format the active build target really got (after import only).</summary>
        public static CheckResult PlatformFormat(Texture2D tex, ConventionsLock c, string assetPath)
        {
            if (tex == null)
                return Result("texture.platform_format", false, Severity.Error, assetPath,
                    "Unity produced no Texture2D from this file (see the import log).");
            var type = MapType(assetPath, c.Textures);
            if (type == null)
                return Result("texture.platform_format", false, Severity.Error, assetPath, "Unknown map type: no expected format.");
            if (EditorUserBuildSettings.activeBuildTarget != BuildTarget.Android)
                return Result("texture.platform_format", true, Severity.Error, assetPath,
                    "Not verifiable: the active build target is not Android (env.build_target).", ("verified", false));
            var expected = c.Textures.Types[type].Formats["android"];
            var actual = tex.format.ToString();
            bool ok = actual == expected;
            return Result("texture.platform_format", ok, Severity.Error, assetPath,
                ok ? $"Android gets {actual}." : $"Android gets {actual}, expected {expected}.",
                ("expected", expected), ("actual", actual), ("verified", true));
        }

        static CheckResult Result(string id, bool passed, string severity, string assetPath, string message,
                                  params (string key, object value)[] metrics)
        {
            var r = new CheckResult { Id = id, Passed = passed, Severity = severity, Message = message, Fixable = false };
            foreach (var (k, v) in metrics) r.Metrics[k] = v;
            if (!passed) r.Offenders.Add(assetPath);
            return r;
        }
    }

    /// <summary>Applies the texture rules to every texture under the managed root.</summary>
    public class TexturePostprocessor : AssetPostprocessor
    {
        public const uint Version = 1;
        public override uint GetVersion() => Version;
        public override int GetPostprocessOrder() => -1000;

        void OnPreprocessTexture()
        {
            ConventionsLock c;
            try { c = ConventionsLock.Load(); }
            catch (ConventionsException) { return; }
            if (!c.UnityImport.Manages(assetPath)) return;
            context.DependsOnCustomDependency(ConventionsDependency.Name);

            var ti = (TextureImporter)assetImporter;
            TextureRulesUnity.Apply(ti, c, assetPath);
            foreach (var r in TextureRulesUnity.SourceChecks(ti, c, assetPath).Where(r => !r.Passed))
            {
                var line = $"artpipe {assetPath}: {r.Id}: {r.Message}";
                if (r.Blocking) context.LogImportError(line);
                else context.LogImportWarning(line);
            }
        }
    }
}
