using System.IO;
using System.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace Artpipe.Tests
{
    /// <summary>
    /// Step 6: textures (backlogs #14, #22, #33). Source images are generated
    /// by the test (no binary fixtures); every expected value comes from the lock.
    /// </summary>
    public class TextureImportTests
    {
        Sandbox _box;
        ConventionsLock C => _box.Conventions;

        [SetUp] public void SetUp() => _box = new Sandbox();
        [TearDown] public void TearDown() => _box.Dispose();

        /// <summary>Write an image into a sandbox class folder; returns its asset path.</summary>
        string Image(string cls, string file, int w, int h, bool alpha)
        {
            var path = $"{_box.ClassFolder(cls)}/{file}";
            var t = new Texture2D(w, h, alpha ? TextureFormat.RGBA32 : TextureFormat.RGB24, false);
            var ext = Path.GetExtension(file).ToLowerInvariant();
            File.WriteAllBytes(path, ext == ".jpg" ? t.EncodeToJPG() : ext == ".tga" ? t.EncodeToTGA() : t.EncodeToPNG());
            Object.DestroyImmediate(t);
            _box.Track(path);
            return path;
        }

        static TextureImporter Importer(string path) => (TextureImporter)AssetImporter.GetAtPath(path);

        CheckResult Source(string path, string id) =>
            TextureRulesUnity.SourceChecks(Importer(path), C, path).Single(r => r.Id == id);

        // --- settings per map type, and the real GPU format ------------------------------

        [Test]
        public void BaseColorIsSrgbWithOpacityAlphaAndAstc()
        {
            var path = Image("prop_small", "T_Crate_D.png", 128, 128, alpha: true);
            Sandbox.Import(path);
            var ti = Importer(path);
            Assert.IsTrue(ti.sRGBTexture);
            Assert.AreEqual(TextureImporterType.Default, ti.textureType);
            Assert.AreEqual(TextureImporterAlphaSource.FromInput, ti.alphaSource);
            Assert.IsTrue(ti.alphaIsTransparency, "D alpha is opacity");
            foreach (var p in TextureRulesUnity.Platforms)
            {
                var ps = ti.GetPlatformTextureSettings(TextureRulesUnity.UnityPlatform(p));
                Assert.IsTrue(ps.overridden, p);
                Assert.AreEqual(C.Textures.Types["D"].Formats[p], ps.format.ToString(), p);
            }
            // Backlog #33: real ASTC, produced headless with -nographics.
            var tex = AssetDatabase.LoadAssetAtPath<Texture2D>(path);
            Assert.IsTrue(TextureRulesUnity.PlatformFormat(tex, C, path).Passed, tex.format.ToString());
        }

        [Test]
        public void NormalMapIsLinearAndUsesTheFinerBlock()
        {
            var path = Image("prop_small", "T_Crate_N.png", 128, 128, alpha: false);
            Sandbox.Import(path);
            var ti = Importer(path);
            Assert.AreEqual(TextureImporterType.NormalMap, ti.textureType);
            Assert.AreEqual(TextureImporterAlphaSource.None, ti.alphaSource);
            var tex = AssetDatabase.LoadAssetAtPath<Texture2D>(path);
            Assert.AreEqual(C.Textures.Types["N"].Formats["android"], tex.format.ToString());
            Assert.AreNotEqual(C.Textures.Types["D"].Formats["android"], C.Textures.Types["N"].Formats["android"],
                "precondition: normal maps use a different (finer) block than base color");
        }

        [Test]
        public void MetallicSmoothnessIsLinearAndKeepsAlphaAsData()
        {
            var path = Image("prop_small", "T_Crate_MS.png", 64, 64, alpha: true);
            Sandbox.Import(path);
            var ti = Importer(path);
            Assert.IsFalse(ti.sRGBTexture, "data maps are linear");
            Assert.IsFalse(ti.alphaIsTransparency, "smoothness in A is data, not opacity");
        }

        [Test]
        public void MaxSizeComesFromTheClassFolder()
        {
            int max = C.Class("prop_small").TextureMaxSize;
            var path = Image("prop_small", "T_Big_D.png", max * 2, max * 2, alpha: false);
            Sandbox.Import(path);
            var tex = AssetDatabase.LoadAssetAtPath<Texture2D>(path);
            Assert.AreEqual(max, tex.width, "shipped at the class max");
            var r = Source(path, "texture.source_size");
            Assert.IsFalse(r.Passed);
            Assert.AreEqual(Severity.Warning, r.Severity, "oversized source is a warning: the importer already caps it");
        }

        [Test]
        public void SharedTextureOutsideClassFoldersGetsTheDefaultMax()
        {
            Assert.AreEqual(C.Textures.DefaultMaxSize,
                TextureRulesUnity.MaxSize(C, C.UnityImport.Materials + "/T_PropsAtlas_D.png"));
            Assert.AreEqual(C.Class("pickup").TextureMaxSize,
                TextureRulesUnity.MaxSize(C, C.UnityImport.Root + "/pickup/coins/T_Coin_D.png"));
        }

        // --- rule violations ---------------------------------------------------------------

        [Test]
        public void NpotIsCaughtBeforeItShipsUncompressed()
        {
            var path = Image("prop_small", "T_Odd_D.png", 96, 64, alpha: false);
            Sandbox.Import(path, "texture.power_of_two");
            Assert.IsFalse(Source(path, "texture.power_of_two").Passed);
            // What the rule prevents (probed): NPOT doesn't get the ASTC format.
            var tex = AssetDatabase.LoadAssetAtPath<Texture2D>(path);
            Assert.IsFalse(TextureRulesUnity.PlatformFormat(tex, C, path).Passed, tex.format.ToString());
        }

        [Test]
        public void MinSizeBoundary()
        {
            int min = C.Textures.MinSize;
            var ok = Image("prop_small", "T_Min_D.png", min, min, alpha: false);
            var small = Image("prop_small", "T_Tiny_D.png", min / 2, min / 2, alpha: false);
            Sandbox.Import(small, "texture.min_size");
            Assert.IsTrue(Source(ok, "texture.min_size").Passed, "exactly the minimum");
            Assert.IsFalse(Source(small, "texture.min_size").Passed, "below it");
        }

        [Test]
        public void AlphaOnANormalMapIsRefused()
        {
            var path = Image("prop_small", "T_Crate_N.png", 64, 64, alpha: true);
            Sandbox.Import(path, "texture.alpha");
            Assert.IsFalse(Source(path, "texture.alpha").Passed);
        }

        [Test]
        public void MetallicSmoothnessWithoutAlphaIsRefused()
        {
            var path = Image("prop_small", "T_Crate_MS.png", 64, 64, alpha: false);
            Sandbox.Import(path, "texture.alpha");
            StringAssert.Contains("needs an alpha channel", Source(path, "texture.alpha").Message);
        }

        [Test]
        public void BadNameIsRefusedAndNotGuessed()
        {
            var path = Image("prop_small", "crate_diffuse.png", 64, 64, alpha: false);
            Sandbox.Import(path, "texture.name");
            Assert.IsFalse(Source(path, "texture.name").Passed);
            Assert.AreEqual(TextureImporterType.Default, Importer(path).textureType);
            Assert.IsFalse(Importer(path).GetPlatformTextureSettings("Android").overridden, "no type-specific settings without a known suffix");
        }

        [Test]
        public void JpgSourceIsRefused()
        {
            var path = Image("prop_small", "T_Crate_D.jpg", 64, 64, alpha: false);
            Sandbox.Import(path, "texture\\.(name|source_format)");
            Assert.IsFalse(Source(path, "texture.source_format").Passed);
        }

        [Test]
        public void TgaIsAccepted()
        {
            var path = Image("prop_small", "T_Crate_AO.tga", 64, 64, alpha: false);
            Sandbox.Import(path);
            Assert.IsTrue(TextureRulesUnity.SourceChecks(Importer(path), C, path).All(r => r.Passed));
        }

        [Test]
        public void TextureOutsideTheRootIsLeftAlone()
        {
            var path = $"{_box.Folder("Assets/" + Sandbox.Marker)}/whatever.jpg";
            var t = new Texture2D(30, 30);
            File.WriteAllBytes(path, t.EncodeToJPG());
            Object.DestroyImmediate(t);
            _box.Track(path);
            Sandbox.Import(path);  // no errors expected
            Assert.IsFalse(Importer(path).GetPlatformTextureSettings("Android").overridden);
        }

        [Test]
        public void ReimportChangesNothing()
        {
            var path = Image("prop_small", "T_Crate_D.png", 64, 64, alpha: true);
            Sandbox.Import(path);
            var meta = File.ReadAllText(path + ".meta");
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
            Assert.AreEqual(meta, File.ReadAllText(path + ".meta"));
        }
    }
}
