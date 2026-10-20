# AI generation

Phase 3 turns a text prompt into a game-ready asset: concept image, then
image-to-3D, then the same Maya validate, export and Unity gate path as a
hand-made asset. This page grows step by step: the ComfyUI setup, the API
behaviours the harness is built on, and the harness itself.

## Models and licences

| Role | Model (file) | Licence | Why |
|---|---|---|---|
| Concept image | Z-Image-Turbo int8 (`z_image_turbo_int8_convrot`, 6.2 GB) | Apache 2.0 | The file ComfyUI's official int8 template uses. int8 runs on Ampere's tensor cores; half the size of bf16, so less offloading on 8 GB. nvfp4 needs Blackwell |
| Text encoder | Qwen3 4B fp8 mixed (5.6 GB) | Apache 2.0 | Same template. The 3070 has no fp8 compute; fp8 only saves storage |
| VAE | `ae.safetensors` (0.3 GB) | Apache 2.0 | Same template |
| Foreground mask | BiRefNet (0.4 GB) | MIT | The mask image-to-3D uses, so the concept checks judge what the 3D model sees |
| Image to 3D | TRELLIS.2 int8 (5.3 GB) + shape and texture VAEs | MIT (code and weights) | Textured, UV-unwrapped mesh with PBR maps; native nodes drop the non-commercial nvdiffrast/nvdiffrec |
| Image encoder for TRELLIS.2 | DINOv3 ViT-L (1.2 GB) | **DINOv3 License** (Meta) | Commercial use allowed, outputs unrestricted; redistributing the weights requires the licence text and "Built with DINOv3". The Comfy-Org repack labels it MIT: the pin records the upstream licence, not the repack's tag |

All files come from Comfy-Org repacks pinned at a repo commit; each sha256 is
the repo's LFS id at that commit (checked again after download).

Ruled out: Hunyuan3D 2.x (licence territory excludes the EU, the UK and South
Korea), Stable Fast 3D (free only under US $1M revenue), FLUX.1-dev
(non-commercial), paid APIs (no budget for this phase). Every component
(image encoder, background remover, text encoder) has its own licence and goes
into the provenance record. TRELLIS.2 on 8 GB VRAM is not confirmed yet; it is
probed before step 3.4 builds on it.

## ComfyUI setup

| | |
|---|---|
| Install | Comfy Desktop 1.1.7, ComfyUI **0.42.1** (commit `63be2f9`), frontend 1.55.18 |
| Python | `%LOCALAPPDATA%\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI\.venv\Scripts\python.exe`: a uv venv (Python 3.13.12, torch 2.12.1+cu130) on top of `standalone-env`. Separate from mayapy |
| Models | `%LOCALAPPDATA%\Comfy-Desktop\ComfyUI-Shared\models`, shared with the Desktop app |
| Pinning | `autoUpdateComfyUI: false` and `autoInstallUpdates: false` in `%APPDATA%\Comfy Desktop\`. An update changes outputs, so it is a deliberate, recorded step |

The pipeline starts ComfyUI headless; the Desktop app stays usable for manual
work, but never at the same time (both want port 8188 and the GPU):

```
.venv\Scripts\python.exe main.py --listen 127.0.0.1 --port 8188
    --disable-auto-launch --disable-all-custom-nodes
    --extra-model-paths-config <yaml> --input-directory <dir>
    --output-directory <dir> --temp-directory <dir> --user-directory <dir>
    --log-stdout
```

`--disable-all-custom-nodes` keeps third-party code out: everything Phase 3
needs is native in 0.42.1. Ready in about 15 s on a warm start.

## ComfyUI behaviours this design relies on

All probed on ComfyUI 0.42.1 through the HTTP API, from **mayapy 3.9 with the
standard library only** (`urllib`, no websocket package):

* `POST /prompt {"prompt": graph, "client_id": id}` takes the **API format**
  graph (`{node_id: {class_type, inputs}}`, links as `[node_id, output_index]`),
  not the UI workflow JSON. It returns `prompt_id` at once and runs
  asynchronously.
* `GET /history/{prompt_id}` is empty until the run ends, then holds `status`
  (`status_str`, `completed`, timestamped `execution_start` /
  `execution_cached` / `execution_success` messages) and `outputs` per node.
  Polling it is enough; the websocket only adds progress, so the harness
  doesn't need it.
* Files are fetched with `GET /view?filename=&subfolder=&type=output`.
* **A graph identical to one already run is not executed again**: every node
  is listed in `execution_cached`, no new file is written, and `outputs`
  points at the **old** file. Changing only the save node's `filename_prefix`
  re-runs just that node. So the harness reads output paths from `history`,
  never guesses them; a "retry with a new seed" must change the seed; and a
  cached run costs zero GPU time, which the cost record must show.
* Output names are `<prefix>_00001_.png`, with a counter per prefix that
  survives restarts (it scans the folder). The harness uses a unique prefix per
  run and copies outputs into its own run folder.
* **`SaveImage` embeds the whole API graph in a PNG `tEXt` chunk (`prompt`)**.
  The file hash then depends on the prefix and every input, not only the
  pixels. The pipeline's server runs with `--disable-metadata` (the 64 px
  smoke PNG drops from 381 to 158 bytes, no text chunk left) and provenance
  lives in the manifest. A live test fails if the flag disappears.
* Invalid graphs are refused **before** running with HTTP 400 and structured
  JSON: `error.type` (`prompt_outputs_failed_validation`, `missing_node_type`)
  plus `node_errors[node_id].errors[]` with a `type` such as
  `value_smaller_than_min` and the input name. That maps to a tool error
  (exit 2) with a machine-readable reason.
* `GET /system_stats` returns the ComfyUI version, package versions, Python and
  torch versions, the exact `argv`, and VRAM total and free. It goes straight
  into the provenance record.
* `GET /object_info` lists every node (987) with its inputs, and `GET
  /models/<folder>` lists the model files ComfyUI sees, so a run can be checked
  before it is queued (missing node or model = tool error, nothing run).
* Native nodes available for the whole chain: `Trellis2Conditioning`,
  `Trellis2ShapeStage`, `Trellis2UpsampleStage` (voxel `target_resolution`
  1024 to 2048, "higher = more VRAM"), `Trellis2TextureStage`,
  `VaeDecodeShapeTrellis`, `VaeDecodeTextureTrellis`, `RemoveBackground`,
  `DecimateMesh` (`target_face_count`, midpoint or QEM placement),
  `RemeshMesh`, `GetMeshInfo`, `BakeNormalMapFromMesh`, `SaveGLB`,
  `TextEncodeZImageOmni`, `UNETLoader`, `CLIPLoader`, `VAELoader`.
* `--deterministic` exists, but its own help says it "might not make images
  deterministic in all cases": the model is the one nondeterministic step.

* A failure **during** execution (probed with a corrupt PNG in `LoadImage`,
  which passes validation because only the file's existence is checked):
  `status_str` is `"error"`, `completed` false, no outputs, and an
  `execution_error` message with `node_id`, `node_type`, `exception_type`
  (`PIL.UnidentifiedImageError`), `exception_message`, a traceback, and
  `executed`: the nodes that finished before it. Nodes after the failing one
  never start. Messages and tracebacks quote absolute paths.
* The same graph gives **identical bytes after a server restart** (no
  in-memory cache, so it really runs twice). True for the model-free smoke
  graph; for a sampler on a GPU it is exactly what can't be promised.

Still to probe with a real model in step 3.4: out of memory on 8 GB, and
`SaveGLB`'s output entry (the `"3d"` key is read from ComfyUI's source).

## The harness: `artpipe gen`

```
artpipe gen run smoke_empty_image --param size 32
# artpipe gen: OK out/gen/runs/smoke_empty_image-1a2b3c4d5e6f/manifest.json
artpipe gen server start|stop|status
```

* **Workflows** live in `gen/workflows/<id>.json`: an API-format graph plus
  the params the pipeline may change (`node`, `input`, `type`, `default`),
  the pinned models it loads, and its output (save) nodes. Any string in the
  graph that looks like a model file (`.safetensors`, `.gguf`...) must be a
  declared model, so nothing loads unhashed.
* **Models** are pinned in `conventions/generation.yaml` by sha256, with their
  licence and download source. A run hashes each file (cached by size and
  mtime: a 6 GB file isn't re-read every run) and refuses a mismatch.
* **ComfyUI** is pinned by version *and* git commit, read from `.git` without
  needing git, and refused if tracked files were edited.
* **Run id** = `<workflow>-<12 hex>`: a hash of the filled graph (params,
  seeds, prompts), the model hashes and the ComfyUI commit. Not the GPU,
  driver or torch build: they change pixels too, but a run id is "what was
  asked"; they are recorded in the manifest. If `out/gen/runs/<run_id>/`
  holds a successful manifest whose files still match their sha256, the run
  is reused without starting ComfyUI.
* **Outputs** go through a per-run prefix (`artpipe/<run_id>/<name>`), are
  located through `/history` only, downloaded through `/view`, and saved as
  `<name>.<ext>`.
* **Manifest** (`conventions/gen_manifest.schema.json`): status, error, workflow
  and its sha256, every param value, models with hash and licence, outputs
  with sha256, environment (ComfyUI version and commit, Python, torch, GPU,
  VRAM, driver, server flags with paths hidden) and execution (prompt id,
  start time, seconds, executed and cached nodes). A refused or failed run
  writes one too: every attempt is on record.
* **Exit codes**: 0 files generated, 2 tool error. 1 belongs to the checks
  that judge the outputs (step 3.3 on).
* `--param NAME VALUE`, not `NAME=VALUE`: `artpipe.cmd` is a batch file, and
  batch splits arguments on `=` (probed: `color=255` arrived as two arguments).

Tests: `tests/test_gen_core.py` and `tests/test_gen_runner.py` run in under a
second on ComfyUI replies recorded from the real server
(`tests/fixtures/comfy/`, refreshed by `artpipe py
tests/fixtures/comfy_record.py`, machine paths scrubbed).
`tests/generation/` (marker `comfy`) runs the real server.

## Concept images: `artpipe gen concept`

```
artpipe gen concept "a golden coin with a star emblem" --class pickup --style casual_toon --seed 1
# artpipe gen: PASS out/gen/runs/concept_zimage-6439e1ea90da/concept.report.json
```

1. **Prompt** = the style's prompt with the subject (`conventions/gen_styles.yaml`)
   + the class sentence + a fixed **framing** contract: one whole object,
   centred with space around it, three-quarter view, plain light grey
   backdrop, even light, no cast shadow, no text. Z-Image-Turbo runs at cfg 1,
   where a negative prompt does nothing, so what to avoid is in the prompt.
   One style per game look; the same style gives every asset of a game the
   same treatment (two shipped: `casual_toon`, `low_poly_flat`).
2. **Workflow** `concept_zimage`: the official int8 template graph (8 steps,
   cfg 1, `res_multistep`/`simple`, AuraFlow shift 3, 1024 px), plus BiRefNet,
   which saves the foreground mask as a second output.
3. **Checks** (`gen/core/concept.py`, thresholds in `generation.yaml`
   `concept`), written to `concept.report.json` in the run folder (the same
   report contract as the Maya stage, tool `artpipe.gen.concept`):
   `concept.background` (the image's border is one plain colour),
   `concept.coverage` (6-70 % of the frame), `concept.margin` (not touching
   the edge), `concept.single_object` (one connected region). Exit 0 usable /
   1 not usable / 2 tool error.

Measured on the RTX 3070 Laptop (8 GB): peak VRAM **7.1 GB**, **~12 s** per
image on a warm server (12.7 s sampling + VAE + mask on the first). The first
run of a session also hashes the 12 GB of models once (then cached by size
and mtime) and starts the server (~15 s).

**Probed: measure the object on the mask, not by colour.** The first coin had
a soft contact shadow despite "no cast shadow". By colour it counted as
object and set the margin to 5 % (the coin itself had ~20 %). BiRefNet's mask
leaves the shadow out, and image-to-3D uses that same mask, so the object
checks read the mask. Recorded (256 px copies in `tests/fixtures/gen/`): this
coin passes, and a three-apples image fails `concept.single_object` (3
regions, largest 34 %).

First batch, seed 1: coin, gem, heart power-up (pickup), crate, traffic cone,
cactus (prop_small), both styles: **6 of 6 pass**, all usable on inspection.
Built to fail: "three separate apples" fails single_object; "a plank cropped
at both ends" came out whole and passes: the framing contract overrode the
subject.

What the checks can't see: the wrong object, a rendered logo, a style that
drifts between two seeds. Those need a human look or a vision-model review
(backlog #48).

## Textures before Unity: `artpipe texture` (backlog #40)

`artpipe texture FILE... [--class C]` runs `validate/core/texture.py`, the
same checks as the Unity package's `TextureRulesUnity.SourceChecks`
(`texture.name`, `source_format`, `power_of_two`, `min_size`, `square`,
`source_size` as a warning, `alpha`), with the same ids, severities and
metrics, from the same conventions. The PNG/TGA reader
(`validate/core/image.py`) is pure Python: headers for the rules, and an
8-bit decoder (all five row filters) for checks on pixels. "Has alpha" means
an alpha channel or a PNG `tRNS` chunk, which is what an engine sees.
**Parity is tested on the real Editor**: eight textures (good, NPOT, too
small, oversized, alpha forbidden or required, TGA) imported into Unity give
the same verdict per check id as Python.

## Image to 3D: `artpipe gen model`

```
artpipe gen model out/gen/runs/concept_zimage-6b48ea88f550 --class pickup
# artpipe gen: PASS out/gen/runs/model_trellis2-cd133b436c13/model.report.json
```

Takes a concept run (refused unless its concept report passed) or any image,
and runs `model_trellis2`: the TRELLIS.2 path of ComfyUI's official template
(Pixal3D and MoGe left out), with the mesh stage rebuilt for mobile budgets.
Decimation target = `tris_max x model.face_budget_share` (0.9), textures
baked at the class's `texture_max_size`. Outputs `model.glb`,
`base_color.png`, `normal.png` and `model.report.json` (tool
`artpipe.gen.model`).

Measured on the RTX 3070 Laptop: peak VRAM **7.96 GB of 8** (dynamic VRAM
offloading does the rest), **~4 min** per model (88 s to 14 min depending on
what else uses the machine).

**Probed: the template's mesh stage can't reach a mobile budget.**

| Stage | Triangles | Open edges | Non-manifold edges |
|---|---|---|---|
| TRELLIS.2 raw | 19.6 M | 104,251 | 247,235 |
| Template remesh (UDF, 768) | 18.6 M | 6 | 53,124 |
| One decimation to 270 (midpoint) | 175 | 21 | 1 |

The coin came out a ring: both flat faces gone. Four variants on the cached
raw mesh: **UDF remesh at 128 with the inner shell dropped, then QEM
decimation** gave 113k closed, manifold triangles and a 270-triangle coin
that keeps its star relief; SDF remeshing broke (1,765 open edges); midpoint
placement was blobby. The 113k mesh is also the high-poly the normal map is
baked from.

**Checks on the GLB** (`gen/core/model.py`, pure Python, `validate/core/glb.py`
reads GLB without dependencies): `model.triangles`, `model.vertices`
(warning), `model.closed`, `model.manifold`, `model.uv`, `model.uv_overlap`,
`model.interior_shells` (warning), `model.textures`.

First batch, six concepts, seed 56: **2 of 6 pass** (gem, cone).

| Asset | Result |
|---|---|
| coin | UV overlap 2.50 % |
| gem | pass (interior shell: 118 of 270 triangles hidden inside, warning) |
| heart | 1 non-manifold edge, UV overlap 0.65 % |
| crate | 9 open edges, 1 non-manifold, UV overlap 2.98 % (thin slats) |
| cone | pass |
| cactus | UV overlap 0.03 % (2-triangle fold, tolerated) |

* **UV overlap is a fold inside a chart, not a packing problem**: 2
  triangles on the cactus at every packing resolution from 256 to 2048 and
  with the `adaptive` segmenter. So the rule is on area: the shared area
  (exact triangle clipping) may be at most `model.uv_overlap_max_share`
  (0.5 %, ~160 texels at 256 px) of the used UV area. The pure-Python test
  finds the same 42 triangles on the coin as Maya's polyUVOverlap.
* **Hollow meshes.** UDF remeshing leaves an inner wall on some shapes even
  with `drop_inverted_components`. A closed shell that faces inward and is
  enclosed by an outward shell is an *interior* shell: invisible, pure
  triangle cost. It is a warning here because the Maya fix deletes it.
* **UV usage is ~50 %**: half of each texture is empty space between charts
  (backlog #50).
* Image-to-3D has no scale: every GLB is ~1 unit. Size comes from the class
  at ingest.

## Into Maya: `artpipe gen ingest`

```
artpipe gen ingest out/gen/runs/model_trellis2-cd133b436c13 --class pickup --name Gem
artpipe build out/gen/assets/Gem/SM_Gem.ma --class pickup --texture out/gen/assets/Gem/T_Gem_D.png --texture out/gen/assets/Gem/T_Gem_N.png
# artpipe build: PASS out/build/SM_Gem/build.report.json -> Assets/Art/pickup/SM_Gem.prefab
```

Refuses a model run whose report didn't pass. Writes
`out/gen/assets/<Name>/SM_<Name>.ma`, `T_<Name>_D.png`, `T_<Name>_N.png` and
`ingest.json` (scale, pivot mode, link to the model manifest by sha256). Then
the ordinary path takes over: generated assets get no shortcut.

* **Weld**: glTF duplicates vertices at every UV seam; Maya gets one vertex
  per position with per-corner UVs, or every seam would be an open border.
* **V flip**: glTF's UV origin is the image's top-left, Maya's and Unity's
  bottom-left.
* **Axes and size**: glTF is metres and +Y up like Maya's default, so no axis
  change (backlog #4 doesn't apply to GLB). The largest dimension is set to
  `ingest.size_cm` per class (pickup 40, prop_small 60) and the mesh placed
  for the class pivot rule.
* **Normals**: the baked normals are set per corner and locked: the normal
  map was baked against exactly those normals.
* **Material**: a plain lambert `M_<Name>`, no file textures. Unity remaps
  the FBX material by name to a shared `Assets/Art/Materials/M_<Name>.mat`
  and never reads textures from the FBX. Probed: file texture nodes make the
  FBX exporter write absolute paths, and the canonical export refuses them
  (fails closed, as designed in Phase 2). Creating that `.mat` is backlog #49.

End to end, the gem (pickup) and the cone (prop_small) pass Maya and Unity:
the gem's interior shell is deleted by the winding fix (270 -> 152
triangles), texel density 95 px/m at 128 px; the cone needs no fix (450
triangles, 173 px/m at 256 px). The only Unity finding is the
`unity.materials` warning (#49).

## New Maya checks (for every asset, not only generated ones)

* **`topology.winding`** (backlog #43): every shell agrees with itself (on a
  manifold edge the two faces run in opposite directions), and no closed
  shell faces inward. Probed: Maya's Conform fixes minority faces but leaves
  a whole inside-out shell of a combined mesh as it is; Maya also reports
  edges between oppositely wound faces as non-manifold and border edges. The
  fix conforms, **deletes interior shells** (inward and enclosed) and
  reverses inside-out ones. A first version reversed interior shells too,
  which would have shipped the gem's 118 hidden triangles.
* **`uv.texel_density`** (backlog #13, warning): texture pixels per metre at
  the class's texture size, faces more than 2x (one mip) off the mesh's
  density, faces whose UVs have no area. Floors pinned from measurements on
  generated assets, one mip below what was measured: pickup 64 px/m (coin
  136, gem 95 at 128 px), prop_small 96 px/m (cone 173 at 256 px). Other
  classes 0 until measured. The default polySphere's lat-long UVs trip the
  2x rule at the poles, which is true, so the sphere fixtures expect it.

## The repair loop: `--repair`

```
artpipe gen model out/gen/runs/concept_zimage-6439e1ea90da --class pickup --repair
# artpipe gen: PASS after 2 attempt(s): all blocking checks pass. See out/gen/repairs/model-e580766c090a.json -> out/gen/runs/model_trellis2-0f90f99d6a35
```

`gen concept --repair` and `gen model --repair` run the stage through
`gen/core/repair.py` with the policy in `generation.yaml` `repair`:

* The **first blocking failure** in the stage's report picks the action:
  `retry` (framing, closed, manifold, UV checks: a new seed changes them) or
  `stop` (`model.triangles`, `model.textures`: the workflow is wrong, a seed
  won't fix it). An unlisted check id stops (`default: stop`): the policy
  never guesses.
* **Seeds are a fixed sequence**, base + n x 1000, so attempt 3 is the same
  request on every machine, and an earlier attempt is reused from its run
  folder instead of spending GPU time again.
* **Bounded**: 3 attempts per stage (a model attempt is ~4 GPU minutes on
  8 GB). Then it hands over: exit 1, with every attempt on record.
* **A tool error always stops** (exit 2): a missing model, a refused graph,
  or a failed concept passed to the model stage. Recorded, not raised.
* **Every attempt is recorded** in `out/gen/repairs/<repair_id>.json`
  (`conventions/gen_repair.schema.json`): seed, run folder, report, the
  blocking checks that failed, the decision and why, ComfyUI seconds, and
  whether the run was reused. `repair_id` hashes the stage and the request
  (inputs by sha256 + base seed): the same request always maps to one record.
* The server stays up between attempts (a cold start is ~15 s) and is
  stopped at the end, unless it was already running or `--keep-server`.

**Measured on the three first-pass failures** (RTX 3070 Laptop, base seed
56, attempt 1 reused from the step 3.4 batch):

| Asset | Seed 56 (step 3.4) | Seed 1056 | Seed 2056 | Result |
|---|---|---|---|---|
| coin | UV overlap 2.50 % | pass (212 s) | | **accepted**, attempt 2 |
| heart | 1 non-manifold edge, UV overlap 0.65 % | pass (102 s) | | **accepted**, attempt 2 |
| crate | 9 open edges, 1 non-manifold, UV overlap 2.98 % | 3 open edges, UV overlap 1.91 % (458 s) | closed and manifold, UV overlap **5.52 %** (447 s) | **handed over** after 3 |

Yield over the six step-3.4 concepts: **3 of 6 first pass** (gem, cone,
cactus) and **5 of 6 after repair**, for 20 GPU minutes of retries. The
crate is the honest failure: every seed fixes more of its topology, and the
last one looks right in the contact sheet, but its thin slats fold inside
their UV charts at every seed (1.9 to 5.5 % shared area). That is a
structural problem of thin parts at a 500-triangle budget, not bad luck, and
the loop does what it should: it stops after three and hands over with every
attempt on record, instead of burning GPU time. Escalation (a finer remesh,
re-unwrapping the folded charts, a new concept) is backlog #53.

(The cactus's stored step-3.4 report said fail: it predates the 0.5 %
overlap rule. Re-checking the reused run gives pass, 0.03 %.)

The contact sheet (concept + three textured views per attempt) agrees with
the checks: the heart's seed-56 model has torn faces and holes, seed 1056 is
a clean closed heart; the coin's retry keeps the star relief; the crate's
split slats show where the folds are.

