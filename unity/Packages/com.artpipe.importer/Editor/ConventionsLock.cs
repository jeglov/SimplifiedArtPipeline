using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Serialization;
using UnityEngine;

namespace Artpipe
{
    /// <summary>A conventions lock that is missing, malformed or older than its sources.</summary>
    public sealed class ConventionsException : Exception
    {
        public ConventionsException(string message) : base(message) { }
    }

    /// <summary>
    /// Typed view of <c>conventions/conventions.lock.json</c>, written by
    /// <c>artpipe conventions --dump</c> from the YAML source of truth.
    ///
    /// No rule value lives in C#: every number comes from here. Values are
    /// already resolved (class defaults merged, texture overrides applied),
    /// so this file holds no rule logic either, only types.
    ///
    /// Loading is strict: an unknown key, a missing key, a wrong lock version
    /// or a source file whose sha256 differs from the one recorded in the lock
    /// all throw <see cref="ConventionsException"/>.
    /// </summary>
    public sealed class ConventionsLock
    {
        public const int SupportedVersion = 1;
        public const string FileName = "conventions.lock.json";

        public int LockVersion { get; set; }
        public string GeneratedBy { get; set; }
        public Dictionary<string, string> Sources { get; set; }
        public UnitRules Units { get; set; }
        public Dictionary<string, AssetClass> Classes { get; set; }
        public TextureRules Textures { get; set; }
        public Dictionary<string, NameRule> Naming { get; set; }
        public FbxExportRules FbxExport { get; set; }

        /// <summary>
        /// The conventions folder: <c>ARTPIPE_CONVENTIONS_DIR</c> if set,
        /// otherwise <c>conventions/</c> next to the Unity project (this repo's layout).
        /// </summary>
        public static string DefaultDirectory()
        {
            var env = Environment.GetEnvironmentVariable("ARTPIPE_CONVENTIONS_DIR");
            if (!string.IsNullOrEmpty(env)) return env;
            var project = Path.GetDirectoryName(Application.dataPath);  // <project>/Assets -> <project>
            return Path.GetFullPath(Path.Combine(project, "..", "conventions"));
        }

        public static ConventionsLock Load() => Load(DefaultDirectory());

        public static ConventionsLock Load(string directory)
        {
            var path = Path.Combine(directory, FileName);
            if (!File.Exists(path))
                throw new ConventionsException($"{FileName} not found in the conventions folder. Run: artpipe conventions --dump");

            ConventionsLock data;
            try
            {
                data = JsonConvert.DeserializeObject<ConventionsLock>(File.ReadAllText(path), Settings);
            }
            catch (JsonException e)
            {
                throw new ConventionsException($"{FileName} is malformed: {e.Message}");
            }
            if (data == null)
                throw new ConventionsException($"{FileName} is empty");
            if (data.LockVersion != SupportedVersion)
                throw new ConventionsException(
                    $"{FileName} has lock_version {data.LockVersion}, this importer reads {SupportedVersion}");

            // Stale lock: someone edited the YAML and didn't re-dump. Refuse,
            // so Unity never applies rules different from what Maya validated.
            foreach (var kv in data.Sources.OrderBy(k => k.Key, StringComparer.Ordinal))
            {
                var source = Path.Combine(directory, kv.Key);
                if (!File.Exists(source))
                    throw new ConventionsException($"{kv.Key} (listed in {FileName}) is missing");
                if (Sha256(source) != kv.Value)
                    throw new ConventionsException(
                        $"{FileName} is stale: {kv.Key} changed since the last dump. Run: artpipe conventions --dump");
            }
            return data;
        }

        public AssetClass Class(string name)
        {
            if (Classes.TryGetValue(name, out var c)) return c;
            // Agent-friendly, like Phase 1: list the valid options so it can self-correct.
            var valid = string.Join(", ", Classes.Keys.OrderBy(k => k, StringComparer.Ordinal));
            throw new KeyNotFoundException($"unknown asset class '{name}', expected one of: {valid}");
        }

        public static string Sha256(string path)
        {
            using (var sha = SHA256.Create())
            using (var stream = File.OpenRead(path))
                return string.Concat(sha.ComputeHash(stream).Select(b => b.ToString("x2")));
        }

        /// <summary>snake_case names, every member required, unknown members rejected.</summary>
        public static readonly JsonSerializerSettings Settings = new JsonSerializerSettings
        {
            ContractResolver = new StrictSnakeCaseResolver(),
            MissingMemberHandling = MissingMemberHandling.Error,
            // Floats like 0.1 must come back as the exact double Python wrote.
            FloatParseHandling = FloatParseHandling.Double,
        };

        sealed class StrictSnakeCaseResolver : DefaultContractResolver
        {
            public StrictSnakeCaseResolver()
            {
                // Dictionary keys (class names, map suffixes, file names) stay as written.
                NamingStrategy = new SnakeCaseNamingStrategy { ProcessDictionaryKeys = false };
            }

            protected override JsonProperty CreateProperty(System.Reflection.MemberInfo member, MemberSerialization ms)
            {
                var p = base.CreateProperty(member, ms);
                if (!p.Ignored) p.Required = Required.Always;  // missing key = error, never a silent default
                return p;
            }
        }
    }

    public sealed class UnitRules
    {
        public string Linear { get; set; }
    }

    public sealed class AssetClass
    {
        public string Description { get; set; }
        public List<string> MeshTypes { get; set; }
        public int TrisMax { get; set; }
        public SizeRange SizeCm { get; set; }
        public LodRules Lods { get; set; }
        public PivotRules Pivot { get; set; }
        public int MaxMaterials { get; set; }
        public UvRules Uv { get; set; }
        public int TextureMaxSize { get; set; }
        public Dictionary<string, string> SeverityOverrides { get; set; }
    }

    public sealed class SizeRange
    {
        public double Min { get; set; }
        public double Max { get; set; }
    }

    public sealed class LodRules
    {
        public int Min { get; set; }
        public int Max { get; set; }
        public List<double> MaxRatio { get; set; }
        public double Tolerance { get; set; }
    }

    public sealed class PivotRules
    {
        public string Mode { get; set; }
        public double ToleranceCm { get; set; }
    }

    public sealed class UvRules
    {
        public List<string> RequiredSets { get; set; }
        public bool UnitSquare { get; set; }
        public bool OverlapAllowed { get; set; }
    }

    public sealed class TextureRules
    {
        public List<string> SourceFormats { get; set; }
        public bool PowerOfTwo { get; set; }
        public bool SquareRequired { get; set; }
        public int MinSize { get; set; }
        public bool Mipmaps { get; set; }
        public Dictionary<string, TextureType> Types { get; set; }
    }

    public sealed class TextureType
    {
        public string Description { get; set; }
        public string ColorSpace { get; set; }
        public string Alpha { get; set; }
        public Dictionary<string, string> Channels { get; set; }
        /// <summary>{platform: format}, e.g. {"android": "ASTC_4x4"}, overrides already applied.</summary>
        public Dictionary<string, string> Formats { get; set; }
    }

    /// <summary>How Maya exported the FBX (conventions/fbx_export.yaml).</summary>
    public sealed class FbxExportRules
    {
        /// <summary>Recorded in every exported report; the import gate compares it.</summary>
        public int SettingsVersion { get; set; }
        public string FileVersion { get; set; }
        public string UpAxis { get; set; }
        public Dictionary<string, bool> Flags { get; set; }
    }

    public sealed class NameRule
    {
        public string Pattern { get; set; }
        public string Description { get; set; }
        public List<string> Examples { get; set; }
        public List<string> InvalidExamples { get; set; }

        Regex _regex;

        /// <summary>
        /// Whole-string match. The schema keeps patterns to the subset shared by
        /// ECMAScript and Python; RegexOptions.ECMAScript enforces that here too
        /// (e.g. \d is ASCII-only). '$' in .NET, as in Python, also matches before
        /// a trailing "\n", so the match length is checked, like re.fullmatch.
        /// </summary>
        public bool IsMatch(string name)
        {
            if (name == null) return false;
            _regex = _regex ?? new Regex(Pattern, RegexOptions.ECMAScript);
            var m = _regex.Match(name);
            return m.Success && m.Index == 0 && m.Length == name.Length;
        }
    }
}
