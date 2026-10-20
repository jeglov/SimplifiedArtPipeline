# AI generation

Phase 3 turns a text prompt into a game-ready asset: concept image, then
image-to-3D, then the same Maya validate, export and Unity gate path as a
hand-made asset. This page grows step by step; today it records the ComfyUI
setup and the API behaviours the harness is built on.

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
  pixels. The harness will run with `--disable-metadata` and keep provenance
  in its own manifest (to be confirmed in step 3.2).
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

Not probed yet: a failure **during** execution (out of memory, a missing model
caught at run time) and what `history` reports for it. Step 3.4 probes it with
a real model, since out of memory is the realistic failure on 8 GB.
