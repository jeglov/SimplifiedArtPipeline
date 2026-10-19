using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.TestTools;

namespace Artpipe.Tests
{
    /// <summary>Step 5: ModelImporter settings per class, LODs, and what the GPU really gets.</summary>
    public class ModelImportTests
    {
        Sandbox _box;

        [SetUp] public void SetUp() => _box = new Sandbox();
        [TearDown] public void TearDown() => _box.Dispose();

        ModelSettings Settings(string cls) => _box.Conventions.UnityImport.Model[cls];

        GameObject ImportGolden(string golden, string cls, string expectError = null) =>
            Sandbox.Import(_box.Put(golden, _box.ClassFolder(cls)), expectError);

        static ModelImporter Importer(GameObject go) =>
            (ModelImporter)AssetImporter.GetAtPath(AssetDatabase.GetAssetPath(go));

        // --- settings from the conventions ------------------------------------------

        [Test]
        public void StaticPropGetsItsClassSettings()
        {
            var go = ImportGolden("SM_Clean", "prop_small");
            var mi = Importer(go);
            var s = Settings("prop_small");
            Assert.AreEqual(s.ReadWrite, mi.isReadable);
            Assert.AreEqual(s.MeshCompression, mi.meshCompression.ToString().ToLowerInvariant());
            Assert.AreEqual(s.Animation, mi.importAnimation);
            Assert.AreEqual(ModelImporterAnimationType.None, mi.animationType, "static props must not get a rig");
            Assert.IsFalse(mi.importCameras || mi.importLights);
            Assert.IsNull(go.GetComponent<Animator>(), "no Animator on a static prop");
            var mesh = ModelChecks.MeshOf(go.GetComponentInChildren<Renderer>());
            Assert.IsFalse(mesh.isReadable, "no CPU copy of the mesh");
            Assert.AreEqual(IndexFormat.UInt16, mesh.indexFormat);
        }

        [Test]
        public void SkinnedCharacterGetsRigAndWeightLimit()
        {
            var go = ImportGolden("SK_Skinned", "character");
            var mi = Importer(go);
            Assert.AreEqual(ModelImporterAnimationType.Generic, mi.animationType);
            Assert.AreEqual(ModelImporterSkinWeights.Custom, mi.skinWeights);
            Assert.AreEqual(Settings("character").SkinWeights, mi.maxBonesPerVertex);
            Assert.IsNotNull(go.GetComponentInChildren<SkinnedMeshRenderer>());
        }

        [Test]
        public void EnvModuleGetsLightmapUvs()
        {
            Assert.IsTrue(Settings("env_module").LightmapUvs, "precondition: env_module asks for lightmap UVs");
            var go = ImportGolden("SM_UVOutsideTiling", "env_module");
            Assert.IsTrue(Importer(go).generateSecondaryUV);
            Assert.IsTrue(ModelChecks.MeshOf(go.GetComponentInChildren<Renderer>()).HasVertexAttribute(VertexAttribute.TexCoord1));
        }

        [Test]
        public void UnitsComeFromTheFile()
        {
            // FBX in cm, file scale converts to metres: the golden 60 cm cube is 0.6 m.
            var go = ImportGolden("SM_Clean", "prop_small");
            Assert.IsTrue(Importer(go).useFileScale);
            Assert.AreEqual(1f, Importer(go).globalScale);
            var size = go.GetComponentInChildren<Renderer>().bounds.size;
            Assert.That(size.x, Is.InRange(0.01f, 10f), "metre range, not centimetres");
        }

        // --- LODs ----------------------------------------------------------------------

        [Test]
        public void LodSwitchHeightsComeFromTheConventions()
        {
            var go = ImportGolden("SM_LodsGood", "prop_medium", "unity.lod_vertex_ratio");  // see FacetedLodIsCaughtOnlyAfterImport
            var lods = go.GetComponent<LODGroup>().GetLODs();
            var rules = _box.Conventions.UnityImport.Lods;
            Assert.AreEqual(2, lods.Length);
            Assert.AreEqual((float)rules.Transitions[0], lods[0].screenRelativeTransitionHeight, 1e-6);
            Assert.AreEqual((float)rules.CullBelow, lods[1].screenRelativeTransitionHeight, 1e-6);
        }

        [Test]
        public void FacetedLodIsCaughtOnlyAfterImport()
        {
            // SM_LodsGood passes Maya (LOD1 = 24% of LOD0's triangles), but LOD1
            // was built with every edge hard: Unity splits it into nearly as many
            // vertices as LOD0. Backlog #11: only the post-import count shows it.
            var go = ImportGolden("SM_LodsGood", "prop_medium", "unity.lod_vertex_ratio");
            var results = ModelChecks.Run(go, _box.Conventions.Class("prop_medium"), AssetDatabase.GetAssetPath(go));
            var ratio = results.Single(r => r.Id == "unity.lod_vertex_ratio");
            Assert.IsFalse(ratio.Passed);
            Assert.Greater((int)ratio.Metrics["lod1_verts"], (int)ratio.Metrics["lod1_verts_max"]);
            Assert.IsTrue(results.Single(r => r.Id == "unity.lod_group").Passed);
            Assert.IsTrue(results.Single(r => r.Id == "unity.vertex_budget").Passed);
        }

        // --- every golden that should pass, passes ------------------------------------------

        [TestCase("SM_Clean", "prop_small")]
        [TestCase("SM_Messy", "prop_small")]
        [TestCase("SM_PickupCentered", "pickup")]
        [TestCase("SM_UVOutsideTiling", "env_module")]
        [TestCase("SK_Skinned", "character")]
        public void GoldenPassesEveryBlockingModelCheck(string golden, string cls)
        {
            var go = ImportGolden(golden, cls);
            var blocking = ModelChecks.Run(go, _box.Conventions.Class(cls), AssetDatabase.GetAssetPath(go))
                .Where(r => r.Blocking).Select(r => r.Id + ": " + r.Message).ToList();
            CollectionAssert.IsEmpty(blocking);
        }

        // --- materials -------------------------------------------------------------------

        [Test]
        public void EmbeddedMaterialIsAWarning()
        {
            var go = ImportGolden("SM_Clean", "prop_small");
            var r = ModelChecks.Run(go, _box.Conventions.Class("prop_small"), AssetDatabase.GetAssetPath(go))
                .Single(x => x.Id == "unity.materials");
            Assert.IsFalse(r.Passed);
            Assert.AreEqual(Severity.Warning, r.Severity);
            CollectionAssert.AreEqual(new[] { "M_Clean" }, r.Offenders);
        }

        [Test]
        public void SharedMaterialIsRemappedByName_EvenWhenAddedLater()
        {
            var fbx = _box.Put("SM_Clean", _box.ClassFolder("prop_small"));
            Assert.AreEqual(fbx, AssetDatabase.GetAssetPath(Sandbox.Import(fbx).GetComponentInChildren<Renderer>().sharedMaterial),
                "before: the FBX's embedded material");

            // The material file is a declared dependency, even while missing:
            // creating it must reimport the FBX and pick it up.
            var path = ModelRules.SharedMaterialPath(_box.Conventions.UnityImport, "M_Clean");
            _box.Folder(Path.GetDirectoryName(path).Replace('\\', '/'));
            AssetDatabase.CreateAsset(new Material(Shader.Find("Universal Render Pipeline/Lit")), path);
            var go = Sandbox.Import(fbx);
            Assert.AreEqual(path, AssetDatabase.GetAssetPath(go.GetComponentInChildren<Renderer>().sharedMaterial));
            Assert.IsTrue(ModelChecks.Run(go, _box.Conventions.Class("prop_small"), fbx).Single(x => x.Id == "unity.materials").Passed);
        }

        // --- idempotence -------------------------------------------------------------------

        [Test]
        public void ReimportChangesNothing()
        {
            var fbx = _box.Put("SM_Messy", _box.ClassFolder("prop_small"));
            Sandbox.Import(fbx);
            var meta = File.ReadAllText(fbx + ".meta");
            var verts = ModelChecks.MeshOf(AssetDatabase.LoadAssetAtPath<GameObject>(fbx).GetComponentInChildren<Renderer>()).vertexCount;

            AssetDatabase.ImportAsset(fbx, ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
            Assert.AreEqual(meta, File.ReadAllText(fbx + ".meta"), ".meta must be byte-identical after a reimport");
            Assert.AreEqual(verts, ModelChecks.MeshOf(AssetDatabase.LoadAssetAtPath<GameObject>(fbx).GetComponentInChildren<Renderer>()).vertexCount);
        }
    }

    /// <summary>ModelChecks budget boundaries on synthetic meshes, sized from the conventions (no numbers in C#).</summary>
    public class ModelCheckBoundaryTests
    {
        readonly List<Object> _objects = new List<Object>();

        [TearDown]
        public void TearDown()
        {
            foreach (var o in _objects) Object.DestroyImmediate(o);
            _objects.Clear();
        }

        GameObject MeshObject(string name, int verts, Transform parent = null, int submeshes = 1)
        {
            var mesh = new Mesh { name = name, vertices = new Vector3[verts] };
            mesh.subMeshCount = submeshes;
            for (int i = 0; i < submeshes; i++) mesh.SetTriangles(new[] { 0, 1, 2 }, i);
            var go = new GameObject(name, typeof(MeshFilter), typeof(MeshRenderer));
            go.GetComponent<MeshFilter>().sharedMesh = mesh;
            if (parent != null) go.transform.SetParent(parent);
            _objects.Add(mesh);
            _objects.Add(go);
            return go;
        }

        GameObject Empty(string name)
        {
            var go = new GameObject(name);
            _objects.Add(go);
            return go;
        }

        static CheckResult Check(GameObject root, AssetClass cls, string id) =>
            ModelChecks.Run(root, cls, "Assets/none.fbx").Single(r => r.Id == id);

        [Test]
        public void VertexBudgetBoundary()
        {
            var cls = ConventionsLock.Load().Class("prop_small");
            Assert.IsTrue(Check(MeshObject("SM_A", cls.VertsMax), cls, "unity.vertex_budget").Passed, "exactly at the budget");
            Assert.IsFalse(Check(MeshObject("SM_B", cls.VertsMax + 1), cls, "unity.vertex_budget").Passed, "one over");
        }

        [Test]
        public void LodVertexRatioBoundary()
        {
            var cls = ConventionsLock.Load().Class("prop_medium");
            const int lod0 = 1000;
            int max = (int)(lod0 * cls.Lods.MaxRatio[1] * (1.0 + cls.Lods.Tolerance));

            var ok = Empty("SM_A");
            MeshObject("SM_A_LOD0", lod0, ok.transform);
            MeshObject("SM_A_LOD1", max, ok.transform);
            Assert.IsTrue(Check(ok, cls, "unity.lod_vertex_ratio").Passed, "exactly at the max");

            var bad = Empty("SM_B");
            MeshObject("SM_B_LOD0", lod0, bad.transform);
            MeshObject("SM_B_LOD1", max + 1, bad.transform);
            Assert.IsFalse(Check(bad, cls, "unity.lod_vertex_ratio").Passed, "one over");
        }

        [Test]
        public void SubmeshBoundary()
        {
            var cls = ConventionsLock.Load().Class("prop_hero");
            Assert.IsTrue(Check(MeshObject("SM_A", 3, submeshes: cls.MaxMaterials), cls, "unity.submeshes").Passed);
            Assert.IsFalse(Check(MeshObject("SM_B", 3, submeshes: cls.MaxMaterials + 1), cls, "unity.submeshes").Passed);
        }

        [Test]
        public void LodNamesWithoutLodGroupFail()
        {
            var cls = ConventionsLock.Load().Class("prop_medium");
            var root = Empty("SM_A");
            MeshObject("SM_A_LOD0", 10, root.transform);
            MeshObject("SM_A_LOD1", 5, root.transform);
            Assert.IsFalse(Check(root, cls, "unity.lod_group").Passed);
        }
    }

    public class GateFolderClassTests
    {
        [TestCase("prop_small", true)]
        [TestCase("pickup", false)]
        [TestCase("", false)]
        [TestCase(null, true)]  // not checked (e.g. a file outside the project)
        public void FolderMustMatchTheReportClass(string folderClass, bool passes)
        {
            var r = ImportGate.Evaluate(Goldens.Fbx("SM_Clean"), ConventionsLock.Load(), folderClass)
                .Single(x => x.Id == "gate.asset_class");
            Assert.AreEqual(passes, r.Passed);
        }
    }
}
