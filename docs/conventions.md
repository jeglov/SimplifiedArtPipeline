# artpipe art conventions

The rules every 3D asset must follow before it goes into a game, why each
rule exists, and how the pipeline checks and fixes them.

**Who this is for:**
- **Artists:** what to deliver.
- **Technical artists and reviewers:** why each rule exists.
- **Agent and tool developers:** check ids, severities and what a fix may touch.

**Where the rules live:** in machine-readable files in [`conventions/`](../conventions/):

| File | Holds |
|---|---|
| [`naming.schema.json`](../conventions/naming.schema.json) | the naming grammar (regexes plus valid and invalid examples) |
| [`mesh_budgets.yaml`](../conventions/mesh_budgets.yaml) | the rules per asset class |
| [`texture_rules.yaml`](../conventions/texture_rules.yaml) | texture formats, map types and import hints |

This document explains those files; it never overrides them. The tables
marked *generated* are rebuilt from the files and the code by
`artpipe docs`, and a test fails if they're out of date. **To change a rule,
edit the file in `conventions/`, then run `artpipe docs` and `artpipe test`.**

---

## 1. Target platform

The budgets assume a **low/mid Android phone** (~3 GB RAM, Mali-G52 /
Adreno 610 class): the device that sets the floor for a live hybrid-casual
game. A rough frame budget at 60 fps is **~150k triangles on screen and
~100 draw calls**. Each class budget below comes from how many copies of that
kind of asset are typically on screen at once: a coin appears 50 times, the
hero once.

> These are informed starting values, not yet measured on a device
> (backlog #23). Profile a test scene on target hardware before relying on
> them for a specific game.

Authoring unit: **centimetres**, Y-up. Unity's *Convert Units* maps cm → m
on import.

## 2. Asset classes

Every asset is validated **as a class**. The class decides its budget, size
range, pivot rule and which checks are relaxed. Pick the class from how the
asset is used in the game, not from what it looks like.

<!-- generated:classes -->
| Class | Use | Prefix | Max tris (LOD0) | Max verts in Unity (LOD0) | LOD levels | Size (cm) | Pivot | Max materials | UV0 inside 0–1 | Max texture (px) | Severity overrides |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `character` | Player / boss character. 1-3 on screen. | `SK_` | 5,000 | 7,500 | 1–3 | 50–300 | bottom_center | 2 | yes | 1024 | — |
| `crowd_unit` | Crowd / army member (runner and crowd games). 50-200 on screen, so the budget is tiny and 1 material is mandatory for GPU instancing. | `SK_` | 800 | 1,200 | 1 | 50–300 | bottom_center | 1 | yes | 256 | — |
| `env_module` | Modular environment kit pieces (floor tiles, walls, track pieces). | `SM_` | 2,000 | 3,000 | 1–2 | 50–5000 | origin | 1 | no (tiling) | 1024 | `topology.open_borders` → warning |
| `pickup` | Coins, gems, collectibles. Dozens on screen, usually spinning. | `SM_` | 300 | 450 | 1 | 5–100 | center | 1 | yes | 128 | — |
| `prop_hero` | Focal props the camera dwells on (chests, upgrade stations). | `SM_` | 3,000 | 4,500 | 1–3 | 20–1000 | bottom_center | 2 | yes | 1024 | — |
| `prop_medium` | Furniture-scale props, obstacles. | `SM_` | 1,500 | 2,250 | 1–2 | 30–400 | bottom_center | 1 | yes | 512 | — |
| `prop_small` | Small set dressing (bottle, cone, crate). Many instances. | `SM_` | 500 | 750 | 1 | 5–150 | bottom_center | 1 | yes | 256 | — |
| `vehicle` | Cars, boats, karts. | `SM_` | 4,000 | 6,000 | 1–3 | 100–1500 | bottom_center | 2 | yes | 1024 | — |

LOD budget (all classes): LOD*n* may have at most 100% / 50% / 25% / 12.5% of LOD0's triangles for n = 0..3, plus 10% tolerance.
Pivot tolerance (all classes): 0.5 cm.
<!-- /generated:classes -->

How to read the table:

- **Max tris** applies to **LOD0**, the version drawn closest to the camera.
  The GPU only draws triangles: a quad counts as 2 and an n-gon as n − 2.
  The real GPU cost is the vertex count *after* Unity splits vertices at UV
  seams and hard edges. Unity measures that after import against `verts_max`
  (see [unity_import.md](unity_import.md#what-the-gpu-really-gets)).
- **Max materials** is per mesh. Each material becomes a submesh, which is
  one draw call per copy on screen, and it breaks GPU instancing.
  200 crowd units × 2 materials = 400 draw calls, four times a low-end
  phone's budget. On mobile this often matters more than triangles.
  Prefer shared atlas materials across props.
- **Size** is the largest bounding-box dimension. It's the main guard
  against unit mistakes. An asset authored in metres comes in 100× too
  small, which is common with AI image-to-3D output.

## 3. Naming

Grammar: `<Type>_<AssetName>[_<Variant>][_LOD<n>]`

| Part | Rule | Examples |
|---|---|---|
| Type | `SM` static mesh, `SK` skinned mesh, `M` material, `T` texture | `SM_`, `SK_` |
| AssetName | PascalCase, letters and digits, 2–32 characters, **no underscores** | `Crate`, `PropsAtlas` |
| Variant | optional: one capital letter or two digits | `_A`, `_01` |
| LOD | `_LOD0` … `_LOD3`, on **all** meshes of the asset or on none | `_LOD1` |

Underscores only ever separate parts, so every name splits the same way.
An agent can parse `SM_Crate_A_LOD2` without guessing.

| What | Valid | Invalid |
|---|---|---|
| Model file | `SM_Crate.fbx`, `SM_Crate_A.fbx`, `SK_Runner_01.fbx` | `sm_crate.fbx`, `SM_Wooden_Crate.fbx`, `SM_Crate_A_LOD0.fbx` |
| Mesh | `SM_Crate`, `SM_Crate_A_LOD0`, `SK_Runner_01_LOD1` | `pCube1`, `SM_Crate_LOD4`, `SM_Crate_lod0` |
| Material | `M_Crate`, `M_PropsAtlas`, `M_Runner_01` | `lambert1`, `MAT_Crate`, `M_Crate_Wood` |
| Texture | `T_Crate_D.png`, `T_Crate_A_N.png`, `T_PropsAtlas_MS.tga` | `T_Crate.png`, `T_Crate_Diffuse.png`, `T_Crate_D.jpg` |

### Hierarchy

- **One root object, named like the file.** All meshes live under it.
  When an FBX has a single root, Unity makes it the prefab root and builds
  the LODGroup on it from the `_LOD<n>` children.
- **Every mesh is named after the file**, as `<file name>` or
  `<file name>_LOD<n>`: one asset per file. Then an agent can find, rename
  or replace an asset across 40 games without opening it.
- **The prefix matches the class.** `SK_` is only for skinned classes.
  A crate named `SK_` would import as a skinned mesh.
- **No stray root objects.** Leftover locators and cameras export as empty
  GameObjects that someone has to delete by hand in every prefab.
  Skeleton root joints are allowed.

## 4. Geometry

| Rule | Why |
|---|---|
| **Manifold.** No edge shared by 3+ faces, no "bowtie" vertex where two parts touch at one point | It can't exist on a real object. Tangents (normal maps), UV unwrapping, lightmap packing and LOD decimation all break on it |
| **No lamina faces** (two faces stacked on the same edges) | You pay triangles for nothing, and they flicker (z-fighting) |
| **Watertight** (no open borders) | Holes show from some camera angles. Inverted-hull outlines and mesh colliders need closed volumes. Kit pieces (`env_module`) may delete unseen faces, so there it's a warning only |
| **Quads and triangles only** (no faces with 5+ sides) | Maya and Unity can split an n-gon into triangles differently, so the mesh shades differently in game than in Maya, and concave n-gons can fold over |
| **No construction history** (modelling nodes) | Live history re-evaluates, so an edit upstream silently changes the mesh. Deformers (skinCluster, blendShape) are allowed |

## 5. UVs

- **The required UV set (`map1`) exists and comes FIRST.** Unity reads UV
  sets by **order, not name**: the first set is UV0 (base color), the
  second is UV1 (lightmap). A file whose first set is "lightmap" imports
  with its texture mapped wrong. Files from other tools often name the set
  `UVChannel_1` or `UVMap`.
- **Every face has UVs.** A face without UVs shows as a flat smear of one
  texel.
- **UV0 stays inside 0–1** (except `env_module`). Props share texture
  atlases, so UVs outside 0–1 sample a neighbour's region. Unity has no
  UDIM support.
- **Overlapping UV0 is allowed** and reported for information only.
  Mirrored or stacked shells double the texel density for free, a standard
  mobile trick. Overlap only matters for lightmaps, and Unity generates UV1
  for those on import.

## 6. Transforms, pivot and scale

- **Frozen transforms**: translate 0, rotate 0, scale 1, no shear, on every
  mesh **and parent group**. Each Maya transform becomes a Unity GameObject.
  Unfrozen values end up inside the prefab, and negative scale renders inside
  out.
- **Pivot = the world origin.** Unity doesn't import Maya pivots: an
  object's pivot in Unity *is* its origin. So the class's pivot mode says
  where the **geometry** sits around (0, 0, 0):

  | Mode | Geometry | For |
  |---|---|---|
  | `bottom_center` | bounding-box bottom-centre on the origin | props and characters: they stand where they're dropped |
  | `center` | bounding-box centre on the origin | pickups: they spin in place |
  | `origin` | no rule | modular kit pieces that snap by a corner |

  Maya pivots must sit at the origin too, so the artist sees the same pivot
  the designer gets.
- **Scene unit cm.** This is only a warning, because Unity converts units
  either way; the real size is enforced by the size check. Up-axis isn't
  checked: Maya doesn't store it in `.ma` files (backlog #4 reads it from
  FBX headers).

## 7. Textures

<!-- generated:textures -->
| Suffix | Map | Color space | Alpha |
|---|---|---|---|
| `_AO` | Ambient occlusion (separate map in URP Lit). | linear | forbidden |
| `_D` | Base color (albedo). | srgb | optional |
| `_E` | Emission. | srgb | forbidden |
| `_MS` | URP Lit metallic map (metallic in R, smoothness in A). | linear | required |
| `_N` | Tangent-space normal map. | linear | forbidden |

Source formats: png, tga. Power of two: yes. Square required: no. Minimum size: 32 px.
<!-- /generated:textures -->

Why: PSD files are huge and slow to import, so the repo takes flattened
exports. JPG adds a second lossy step before the GPU's own (ASTC)
compression. Normal maps use the **OpenGL** convention (green = Y+), which
is what Unity expects; Substance's Unreal preset exports DirectX normals and
gives inverted lighting. Maximum sizes per class are in the class table
above. Textures are validated and imported by the Unity package (see
[unity_import.md](unity_import.md#textures)).

## 8. What the validator checks

<!-- generated:checks -->
| # | Check id | What it enforces | Default severity | Auto-fix |
|---|---|---|---|---|
| 1 | `scene.has_meshes` | The file contains at least one polygon mesh. | error | — |
| 2 | `topology.non_manifold` | No edges shared by more than two faces and no bowtie vertices. | error | — |
| 3 | `topology.lamina_faces` | No duplicate faces that share all of their edges with another face. | error | yes |
| 4 | `topology.open_borders` | The mesh is watertight: no border edges (holes). | error | — |
| 5 | `topology.ngons` | No polygons with more than 4 sides. | error | — |
| 6 | `budget.tris` | LOD0 triangle count is within the asset class budget. | error | — |
| 7 | `budget.lods` | LOD count is allowed for the class, levels are contiguous and each LOD is light enough. | error | — |
| 8 | `budget.materials` | Every mesh has at least one and at most max_materials materials. | error | — |
| 9 | `uv.present` | Required UV sets exist, come first in the set order, and map every face. | error | yes |
| 10 | `uv.unit_square` | All UV0 coordinates are inside the 0-1 square (unless the class allows tiling). | error | — |
| 11 | `uv.overlap` | UV0 faces don't overlap (reported as info where the class allows overlap). | info (error where the class sets overlap_allowed: false) | — |
| 12 | `scene.units` | The scene's linear unit matches the conventions (cm). Not verifiable for FBX yet. | warning | yes |
| 13 | `transform.frozen` | Meshes and their parent groups have frozen transforms (t=0, r=0, s=1, no shear). | error | yes |
| 14 | `transform.pivot` | Pivots are at the world origin and the geometry is placed per the class pivot mode. | error | yes |
| 15 | `transform.size` | The largest dimension (cm) is within the asset class size range. | error | — |
| 16 | `naming.convention` | Names follow naming.schema.json; one root named like the file; consistent LOD suffixes. | error | yes |
| 17 | `scene.history` | Meshes have no construction (modelling) history; deformers are allowed. | error | yes |
<!-- /generated:checks -->

**Severity:**

| Severity | Meaning |
|---|---|
| `error` | blocks import into Unity |
| `warning` | import allowed, a human or agent should look |
| `info` | reported for awareness, never fails the asset |

Classes can change a check's severity (see "Severity overrides" in
section 2). `uv.overlap` decides its own severity from the class: info
where overlap is allowed, error where it isn't.

## 9. What the auto-fixer may change

`--fix` applies only fixes that are **deterministic** and that either leave
the visible result unchanged or apply a change the convention fully
defines. It saves a copy (never the source), **re-opens the saved file and
validates it from scratch**, and the report lists every fix applied or
skipped, with its reason.

<!-- generated:fixes -->
| Order | Fixes check | What it changes |
|---|---|---|
| 1 | `topology.lamina_faces` | Delete the duplicate (stacked) faces reported by the check. |
| 2 | `scene.history` | Bake construction history into the mesh (deformers are kept). |
| 3 | `uv.present` | Rename a lone UV set to the required name, or move the required set first. |
| 4 | `scene.units` | Set the scene's linear unit to the conventions unit (geometry is unchanged). |
| 5 | `transform.frozen` | Freeze transforms on the asset hierarchy and reset pivots to the origin. |
| 6 | `transform.pivot` | Reset pivots to the origin and move the geometry so its class anchor sits there. |
| 7 | `naming.convention` | Rename default/invalid mesh, root group and material names after the file. |
<!-- /generated:fixes -->

**A fix refuses to run, and says why, when it can't be sure:**
- the freeze fix refuses negative scale: freezing it flips the normals in a
  way FBX doesn't carry, so the mesh imports inside out
- the freeze, pivot and lamina fixes refuse skinned meshes
- the UV fix refuses faces with no UVs: those need an unwrap
- the naming fix refuses a valid name that just disagrees with the file:
  `SM_Crate` inside `SM_FileMismatch.ma` could be either mistake

**Never auto-fixed:** holes, non-manifold geometry, n-gons, over-budget
triangles, LODs or materials, wrong size, UVs outside 0–1. Each of these
needs modelling judgement. The report says exactly where the problem is,
and a human decides.

## 10. Running it

```powershell
.\artpipe.cmd validate SM_Crate.ma --class prop_small                 # report -> out\SM_Crate.ma.report.json
.\artpipe.cmd validate SM_Crate.ma --class prop_small --fix           # fixed copy -> out\fixed\SM_Crate.ma
.\artpipe.cmd validate *.ma --class prop_small --max-offenders 0      # many files, full offender lists
```

**Exit codes:** `0` the asset passes, `1` the asset fails (fix the asset),
`2` the tool failed (retry or alert a human). With `--fix`, the code
describes the fixed file.

**Reports:** one JSON report per input. The output is deterministic: the
same file always gives byte-identical bytes, with no timestamps and with
repo-relative paths. Each check result has this shape:

```json
{"id": "topology.ngons", "passed": false, "severity": "error", "fixable": false,
 "message": "2 ngons (up to 8 sides) on 1 mesh",
 "metrics": {"ngons": 2, "max_sides": 8, "meshes_affected": 1},
 "offenders": ["|SM_Ngon.f[8]", "|SM_Ngon.f[9]"], "offender_count": 2}
```

`offenders` names exactly what to look at (faces, edges, nodes). An agent
can pass it straight to Maya.
