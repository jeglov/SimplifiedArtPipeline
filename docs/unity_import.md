# Unity import

How an asset gets from a validated Maya scene into the Unity project, what
is checked on the way, and why. Written for artists (what to deliver), TAs
(why each rule exists) and agent developers (ids, exit codes, reports).

Tables marked *generated* come from `conventions/` (`artpipe docs`), and a
test fails if they drift. Everything else is hand-written.

## The chain

```
SM_Crate.ma ──► Maya stage ──────────────────────────► Unity stage ───────────────────────────► SM_Crate.prefab
               validate, safe fixes, re-validate        gate (outside the project), import with
               export canonical FBX + its own report     class rules, measure, Prefab Variant
```

```powershell
.\artpipe.cmd build SM_Crate.ma --class prop_small --texture T_Crate_D.png   # both stages, one report
.\artpipe.cmd unity import --class prop_small out\export\SM_Crate.fbx        # Unity stage only
.\artpipe.cmd unity verify                                                   # re-check everything in the project
```

Every command returns `0` pass, `1` an asset failed (fix the asset), `2` the
tool failed (retry or alert a human), and writes a deterministic JSON report:

| Report | Written by | Contract |
|---|---|---|
| `<file>.report.json` | Maya stage, one per scene and one per exported FBX | [`report.schema.json`](../conventions/report.schema.json) |
| `import.report.json` / `verify.report.json` | Unity stage | [`unity_report.schema.json`](../conventions/unity_report.schema.json) |
| `build.report.json` | `artpipe build` | [`build_report.schema.json`](../conventions/build_report.schema.json) |

The build report links each stage report by path, and the FBX by sha256
across all three.

## Where assets live

```
Assets/Art/
  prop_small/      SM_Crate.fbx  SM_Crate.fbx.report.json  SM_Crate.prefab  T_Crate_D.png
  pickup/          ...
  Materials/       M_PropsAtlas.mat  T_PropsAtlas_D.png      (shared, outside class folders)
```

* The pipeline only touches files under `Assets/Art`
  (`unity_import.yaml: root`). Store packages and prototypes elsewhere are
  never changed.
* **The folder is the asset class.** Budgets come from it, and the gate
  refuses an FBX whose folder doesn't match the class Maya validated it as.
* **Game code references the `.prefab`**, a Prefab Variant of the model
  created once by the import. It follows every FBX reimport and can carry
  gameplay components. An existing prefab is never overwritten.
* An FBX material `M_Crate` is remapped to `Assets/Art/Materials/M_Crate.mat`
  when that file exists (shared materials, fewer SRP Batcher variants).
  Creating the material later reimports the FBX.

## The import gate

An FBX is accepted only with its **sidecar report** (`X.fbx.report.json`,
written by `artpipe validate --export`) next to it. `artpipe unity import`
judges the file **before** it enters the project, so a refused file never
reaches `Assets/`. A file dropped into `Assets/Art` by hand is judged on
import and, if refused, flagged with an import error **and made inert**
(renderers and LOD groups stripped): Unity can't cancel an import, so the
asset still exists, but it can't ship looking normal.

The verdict is cached by Unity's import pipeline, so every input it reads is
declared as an import dependency: the sidecar, the shared material files, the
conventions (a custom dependency hashing `conventions/`) and the
postprocessor version. `artpipe unity verify` re-evaluates everything from
the files, with no cache involved.

## Model import settings

Applied to every FBX on every import from `unity_import.yaml` (through the
lock). An Inspector change is overwritten on the next import, and that is
deliberate: a setting set by hand is invisible in review and lost on the next
import with defaults.

Per class *(generated)*:

<!-- generated:unity_classes -->
| Class (folder) | Max GPU verts (LOD0) | Max submeshes | Max LODs | Max texture (px) | Rig | Bone influences | Lightmap UVs |
|---|---|---|---|---|---|---|---|
| `character` | 7,500 | 2 | 3 | 1024 | generic | 4 | no |
| `crowd_unit` | 1,200 | 1 | 1 | 256 | generic | 2 | no |
| `env_module` | 3,000 | 1 | 2 | 1024 | none | — | yes |
| `pickup` | 450 | 1 | 1 | 128 | none | — | no |
| `prop_hero` | 4,500 | 2 | 3 | 1024 | none | — | no |
| `prop_medium` | 2,250 | 1 | 2 | 512 | none | — | no |
| `prop_small` | 750 | 1 | 1 | 256 | none | — | no |
| `vehicle` | 6,000 | 2 | 3 | 1024 | none | — | no |
<!-- /generated:unity_classes -->

Same for every class *(generated)*:

<!-- generated:unity_model_defaults -->
| Setting (same for every class) | Value |
|---|---|
| `animation` | false |
| `blend_shapes` | false |
| `cameras` | false |
| `lights` | false |
| `mesh_compression` | medium |
| `normals` | import |
| `optimize_mesh` | true |
| `read_write` | false |
| `tangents` | calculate_mikk |
| `visibility` | false |
<!-- /generated:unity_model_defaults -->

Why the less obvious ones:

* **Read/Write off:** otherwise Unity keeps a CPU copy of every mesh: double memory.
* **rig `none` for static classes.** Unity's default is Generic with
  animation import, which gives a static prop an Animator and an empty clip.
* **Bone influences:** 4 for characters, **2 for crowd units** (50–200 on
  screen, so skinning cost per vertex matters).
* **Units:** the FBX is in cm and its file scale converts to metres. Never
  fix scale with `globalScale`.
* **Triangulation happens at export** (`fbx_export.yaml`), so the normal-map
  baker, the validator and Unity all see the same triangles.

LOD switch heights *(generated)*. Unity builds the LODGroup from `_LODn` names:

<!-- generated:unity_lods -->
| Switch | Screen height |
|---|---|
| LOD0 → LOD1 | 0.4 |
| LOD1 → LOD2 | 0.15 |
| LOD2 → LOD3 | 0.05 |
| last LOD culled below | 0.01 |
<!-- /generated:unity_lods -->

## What the GPU really gets

Maya counts a vertex once. Unity splits it at every **UV seam and hard
edge**, and that split count is what the GPU processes. So vertex budgets are
measured **after** import. The class table above holds the vertex budget
(`verts_max`), and every LOD must also cut vertices by its LOD ratio
(`mesh_budgets.yaml`).

Example from the fixtures: `SM_LodsGood` passes Maya (LOD1 has 24 % of
LOD0's triangles), but after import LOD1 has **87 % of LOD0's vertices**.
It was built with every edge hard, so the LOD saves almost no vertex work.
`unity.lod_vertex_ratio` fails it. No Maya-side check can see this.

## Textures

Name: `T_<Name>[_Variant]_<suffix>.png|tga`. The suffix decides everything
else *(generated)*:

<!-- generated:unity_textures -->
| Suffix | Map | Color space | Alpha | Android | iOS |
|---|---|---|---|---|---|
| `_AO` | Ambient occlusion (separate map in URP Lit). | linear | forbidden | ASTC_6x6 | ASTC_6x6 |
| `_D` | Base color (albedo). | srgb | optional | ASTC_6x6 | ASTC_6x6 |
| `_E` | Emission. | srgb | forbidden | ASTC_6x6 | ASTC_6x6 |
| `_MS` | URP Lit metallic map (metallic in R, smoothness in A). | linear | required | ASTC_6x6 | ASTC_6x6 |
| `_N` | Tangent-space normal map. | linear | forbidden | ASTC_4x4 | ASTC_4x4 |

Mipmaps: yes. Power of two: yes. Minimum size: 32 px. Outside class folders (shared atlases): max 512 px.
<!-- /generated:unity_textures -->

* `_D` alpha is opacity (edges dilated). `_MS` alpha is smoothness (data, left alone).
* Normal maps use the finer ASTC block: at 6×6 the compression error shows as faceted lighting.
* A texture with an unknown suffix gets **no** type-specific settings: it is
  reported, never guessed.
* Non-power-of-two is an error for a measured reason: a 300×200 PNG silently
  imported as **uncompressed RGBA32**, about 4.5× the memory of ASTC 6×6.
  `texture.platform_format` checks the format Android really got.

## Checks

Every result has the Phase 1 shape: `{id, passed, severity, message, fixable, metrics, offenders}`.
A test fails if a check id in the C# code is missing from this table, or if
the table lists an id the code no longer has.

| Check id | Severity | What it enforces |
|---|---|---|
| `env.build_target` | error (tool) | The active build target is Android, so textures really get ASTC. It is stored in `Library/`, so every run passes `-buildTarget Android`. |
| `env.color_space` | error (tool) | The project is Linear; in Gamma the texture sRGB flag is ignored. |
| `env.conventions` | error (tool) | `conventions.lock.json` loads strictly and matches the hashes of its YAML sources. |
| `env.render_pipeline` | error (tool) | URP is the active pipeline (materials target URP Lit). |
| `env.serialization` | error (tool) | Assets are serialized as text (diffable, deterministic). |
| `gate.report_present` | error | The FBX has its sidecar report. |
| `gate.report_readable` | error | The report is a supported schema version with no unknown or missing keys. |
| `gate.report_passed` | error | Maya validation passed; the message lists the failed checks. |
| `gate.hash_match` | error | The FBX is byte-identical to the file Maya validated. |
| `gate.export_settings` | error | It is an export report, written with the current `fbx_export.yaml` settings version. |
| `gate.asset_class` | error | The class is known and matches the folder the FBX sits in. |
| `unity.imported` | error | Unity produced a model from the FBX. |
| `unity.vertex_budget` | error | GPU vertices of LOD0 (or of the whole asset) ≤ the class `verts_max`. |
| `unity.lod_vertex_ratio` | error | Each LOD has at most LOD0's vertices × its LOD ratio (+ tolerance). |
| `unity.submeshes` | error | Submeshes per mesh ≤ the class `max_materials` (each one is a draw call). |
| `unity.lod_group` | error | A LODGroup exists exactly when there are `_LODn` meshes, with matching levels. |
| `unity.materials` | warning | Every material is a shared project material, not one embedded in the FBX. |
| `unity.prefab` | warning (error if broken) | The model has its Prefab Variant, and it really derives from this model. |
| `texture.name` | error | `T_<Name>[_Variant]_<suffix>` with a known suffix. |
| `texture.source_format` | error | The source is PNG or TGA (lossless, flattened). |
| `texture.power_of_two` | error | Both dimensions are a power of two. |
| `texture.min_size` | error | At least the minimum size. |
| `texture.square` | error | Square, when the conventions require it. |
| `texture.source_size` | warning | The source fits the max size (the importer caps it anyway). |
| `texture.alpha` | error | An alpha channel is present or absent as the map type requires. |
| `texture.platform_format` | error | The imported texture really has the expected Android format. |

`env.*` failures are **tool errors** (exit 2): the asset can't be blamed for
a misconfigured project.

## Determinism

Same input, same bytes, on any machine:

* **FBX** is exported as ASCII and canonicalized: timestamps pinned, absolute
  paths (including the artist's user folder) reduced to file names, object
  IDs renumbered in declaration order.
* **New assets get GUIDs derived from their path**, so prefab and scene
  references are identical everywhere. Unity writes its full `.meta` first
  and the GUID line is swapped afterwards. A pre-written GUID-only `.meta`
  makes Unity skip writing importer settings (a PNG imported as a Cubemap).
* **New Prefab Variants** get a path-derived PrefabInstance file ID instead
  of Unity's random one.
* **Reimports change nothing**, `.meta` files included (tested).

## Unity behaviours this design relies on

All probed on Unity 6000.5 before building on them:

* An `AssetPostprocessor` can't cancel an import. `LogImportError` only flags it.
* An import reruns only when a **declared** dependency changes. Otherwise
  Unity reuses the cached result and replays its stored log, old errors
  included.
* Changing a postprocessor's code alone does not reimport existing assets:
  bump `GetVersion()`.
* Settings set in `OnPreprocessModel` / `OnPreprocessTexture` apply to that
  same import and are written to the `.meta`.
* An FBX and its sidecar arriving in the same refresh import the FBX twice.
  The CLI imports sidecars first.
* Unity builds the LODGroup from `_LODn` names by itself.
* `-nographics` (no GPU device) still produces real ASTC for the Android target.
* Batchmode exits `1` on a crash, a compile error or a locked project alike.
  The runner tells them apart from "asset failed".
* A second Editor on the same project makes batchmode exit `1` with nothing
  in the log. The runner detects the lock file first.
* `Unity -createProject` makes a Gamma project.
* Unity's working directory is the project folder, so all paths passed to it are absolute.

Known gaps are in [backlog.md](backlog.md) (stages P2–P5).
