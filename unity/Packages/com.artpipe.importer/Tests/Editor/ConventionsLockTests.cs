using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace Artpipe.Tests
{
    /// <summary>
    /// Step 2: the C# loader for conventions.lock.json. No rule value is typed
    /// here: tests compare against the lock file itself, so changing a budget
    /// in YAML never requires touching C#.
    /// </summary>
    public class ConventionsLockTests
    {
        string _tmp;

        [TearDown]
        public void TearDown()
        {
            if (_tmp != null && Directory.Exists(_tmp)) Directory.Delete(_tmp, true);
            _tmp = null;
        }

        /// <summary>A writable copy of the repo's conventions/ folder, to break on purpose.</summary>
        string CopyConventions()
        {
            _tmp = Path.Combine(Path.GetTempPath(), "artpipe-conv-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(_tmp);
            foreach (var f in Directory.GetFiles(ConventionsLock.DefaultDirectory()))
                File.Copy(f, Path.Combine(_tmp, Path.GetFileName(f)));
            return _tmp;
        }

        static string LockPath(string dir) => Path.Combine(dir, ConventionsLock.FileName);

        static void EditLock(string dir, Action<JObject> change)
        {
            var json = JObject.Parse(File.ReadAllText(LockPath(dir)));
            change(json);
            File.WriteAllText(LockPath(dir), json.ToString());
        }

        [Test]
        public void RepoLockLoads()
        {
            var c = ConventionsLock.Load();
            Assert.AreEqual(ConventionsLock.SupportedVersion, c.LockVersion);
            Assert.IsNotEmpty(c.Classes);
        }

        [Test]
        public void RoundTripPreservesEveryValue()
        {
            // Every key in the file maps to a typed member and back, unchanged.
            // A renamed or forgotten member breaks this (or the strict load).
            var file = JToken.Parse(File.ReadAllText(LockPath(ConventionsLock.DefaultDirectory())));
            var back = JToken.Parse(JsonConvert.SerializeObject(ConventionsLock.Load(), ConventionsLock.Settings));
            Assert.IsTrue(JToken.DeepEquals(file, back),
                "round trip differs:\n" + back.ToString());
        }

        [Test]
        public void FloatsKeepTheirExactValue()
        {
            // 0.1 and 0.125 must be the same doubles Python wrote, not decimals or floats.
            var c = ConventionsLock.Load();
            var file = JObject.Parse(File.ReadAllText(LockPath(ConventionsLock.DefaultDirectory())));
            foreach (var kv in c.Classes)
            {
                var ratios = file["classes"][kv.Key]["lods"]["max_ratio"].Select(t => (double)t).ToList();
                CollectionAssert.AreEqual(ratios, kv.Value.Lods.MaxRatio);
                Assert.AreEqual((double)file["classes"][kv.Key]["lods"]["tolerance"], kv.Value.Lods.Tolerance);
            }
        }

        [Test]
        public void NamingExamplesBehaveTheSameInDotNet()
        {
            // The same examples Python's tests enforce: proves the regexes mean
            // the same thing in .NET as in Python's re.
            foreach (var kv in ConventionsLock.Load().Naming)
            {
                foreach (var ok in kv.Value.Examples)
                    Assert.IsTrue(kv.Value.IsMatch(ok), $"{kv.Key}: '{ok}' should match");
                foreach (var bad in kv.Value.InvalidExamples)
                    Assert.IsFalse(kv.Value.IsMatch(bad), $"{kv.Key}: '{bad}' should not match");
            }
        }

        [Test]
        public void TrailingNewlineNeverMatches()
        {
            // Boundary: '$' matches before a final "\n" in .NET too.
            foreach (var kv in ConventionsLock.Load().Naming)
                foreach (var ok in kv.Value.Examples)
                    Assert.IsFalse(kv.Value.IsMatch(ok + "\n"), $"{kv.Key}: '{ok}\\n' should not match");
        }

        [Test]
        public void UnknownClassListsTheValidOnes()
        {
            var c = ConventionsLock.Load();
            var e = Assert.Throws<KeyNotFoundException>(() => c.Class("prop_smal"));
            StringAssert.Contains("prop_small", e.Message);
        }

        [Test]
        public void StaleSourceIsRefused()
        {
            var dir = CopyConventions();
            File.AppendAllText(Path.Combine(dir, "mesh_budgets.yaml"), "# edited, not re-dumped\n");
            var e = Assert.Throws<ConventionsException>(() => ConventionsLock.Load(dir));
            StringAssert.Contains("stale: mesh_budgets.yaml", e.Message);
        }

        [Test]
        public void MissingSourceIsRefused()
        {
            var dir = CopyConventions();
            File.Delete(Path.Combine(dir, "naming.schema.json"));
            StringAssert.Contains("naming.schema.json",
                Assert.Throws<ConventionsException>(() => ConventionsLock.Load(dir)).Message);
        }

        [Test]
        public void MissingLockIsRefused()
        {
            var dir = CopyConventions();
            File.Delete(LockPath(dir));
            StringAssert.Contains("not found",
                Assert.Throws<ConventionsException>(() => ConventionsLock.Load(dir)).Message);
        }

        [Test]
        public void UnknownKeyIsRefused()
        {
            // A key C# doesn't know means the lock is newer than this importer:
            // ignoring it would silently drop a rule.
            var dir = CopyConventions();
            EditLock(dir, j => ((JObject)j["classes"]["prop_small"])["tris_max_v2"] = 1);
            StringAssert.Contains("tris_max_v2",
                Assert.Throws<ConventionsException>(() => ConventionsLock.Load(dir)).Message);
        }

        [Test]
        public void MissingKeyIsRefused()
        {
            // Without Required.Always, a missing tris_max would load as 0.
            var dir = CopyConventions();
            EditLock(dir, j => ((JObject)j["classes"]["prop_small"]).Remove("tris_max"));
            StringAssert.Contains("tris_max",
                Assert.Throws<ConventionsException>(() => ConventionsLock.Load(dir)).Message);
        }

        [Test]
        public void OtherLockVersionIsRefused()
        {
            var dir = CopyConventions();
            EditLock(dir, j => j["lock_version"] = ConventionsLock.SupportedVersion + 1);
            StringAssert.Contains("lock_version",
                Assert.Throws<ConventionsException>(() => ConventionsLock.Load(dir)).Message);
        }

        [Test]
        public void MalformedJsonIsRefused()
        {
            var dir = CopyConventions();
            File.WriteAllText(LockPath(dir), "{ not json");
            StringAssert.Contains("malformed",
                Assert.Throws<ConventionsException>(() => ConventionsLock.Load(dir)).Message);
        }
    }
}
