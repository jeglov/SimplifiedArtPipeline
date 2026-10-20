# AI generation

Phase 3 turns a text prompt into a game-ready asset: concept image, then
image-to-3D, then the same Maya validate, export and Unity gate path as a
hand-made asset. This page grows step by step: the ComfyUI setup, the API
behaviours the harness is built on, and the harness itself.

## Models and licences

| Role | Model | Licence | Why |
|---|---|---|---|
| Concept image | Z-Image-Turbo (fp8) | Apache 2.0 | 8 steps, community-reported to fit 8 GB |
| Image to 3D | TRELLIS.2, native ComfyUI nodes | MIT (code and weights) | Textured, UV-unwrapped mesh with PBR maps; native nodes drop the non-commercial nvdiffrast/nvdiffrec |

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
