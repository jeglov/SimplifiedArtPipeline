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
    /// Registered on every domain load and by the batchmode CLI before it imports.
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
}
