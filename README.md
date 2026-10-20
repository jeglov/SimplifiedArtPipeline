# artpipe

An agent-runnable art pipeline for live mobile games. Every step runs
headless and returns machine-readable JSON, so an AI agent can do the whole
loop with no human in it: generate an asset, validate it, fix what is safe
to fix, and import it into Unity.

> **Status: Phases 1 and 2 complete.** A headless Maya validator with safe
> auto-fixes, deterministic FBX export, and a Unity 6 import pipeline with an
> import gate, per-class import rules, GPU budgets and a batchmode CLI. One
> command takes a messy Maya scene to a clean Unity prefab. Next: the AI
> generation stage (Phase 3). See the [roadmap](#roadmap).

## What it does today

```powershell
.\artpipe.cmd build out\fixtures\SM_Messy.ma --class prop_small --texture T_Messy_D.png
# artpipe build: PASS out/build/SM_Messy/build.report.json -> Assets/Art/prop_small/SM_Messy.prefab
```

`SM_Messy` is a rushed export with six problems: default names, a foreign UV
set name, live history, an unfrozen and off-centre transform, a metre scene.
In about 20 seconds:

1. **Maya** (headless `mayapy`) runs 18 checks against the rules for its
   class, applies the 6 safe fixes, saves a fixed copy, and validates it again
   from scratch. Holes, n-gons and over-budget meshes are never "fixed":
   the build stops and the report says where they are.
2. It **exports a canonical FBX** with explicit, verified exporter settings.
   Same scene, same bytes: no timestamps, no absolute paths, stable object
   IDs. Then it validates that FBX too and writes its report next to it.
3. **Unity** (batchmode) gates the FBX: it needs a passing report that
   describes these exact bytes. It then imports it with the class's import
   settings, measures what the GPU really gets (vertices after seam splits,
   LOD vertex ratios, submeshes), imports the texture as real ASTC, and
   creates a Prefab Variant.
4. One **build report** links every stage by path and sha256. Every command
   returns `0` pass / `1` asset failed / `2` tool error.

## The rules

All rules live in [`conventions/`](conventions/), not in code. C# reads them
from a generated, hash-checked `conventions.lock.json`, and never holds a
budget number.

| File | Holds |
|---|---|
| [`mesh_budgets.yaml`](conventions/mesh_budgets.yaml) | 8 asset classes for low/mid Android: triangle and GPU-vertex budgets, LODs, materials, size, pivot, UVs, severity overrides |
| [`naming.schema.json`](conventions/naming.schema.json) | the naming grammar `<SM\|SK>_<AssetName>[_Variant][_LODn]`, with valid and invalid examples that Python and C# tests both enforce |
| [`texture_rules.yaml`](conventions/texture_rules.yaml) | map types (`_D _N _MS _AO _E`): color space, alpha rules, ASTC block per platform, max size per class |
| [`fbx_export.yaml`](conventions/fbx_export.yaml) | every FBX exporter setting, versioned |
| [`unity_import.yaml`](conventions/unity_import.yaml) | managed folder, shared materials, LOD switch heights, ModelImporter settings per class |
| `*.schema.json` | the report contracts: Maya, Unity and build reports |

* **[docs/conventions.md](docs/conventions.md)** explains every Maya-side
  rule and why it exists.
* **[docs/unity_import.md](docs/unity_import.md)** covers the Unity side:
  folder layout, gate, import settings, GPU budgets, textures, every check id,
  and the Unity behaviours the design relies on.

Tables in both docs are generated from these files, and tests fail if the docs drift.

## Quick start

Requirements: Windows, Maya 2023+ (`mayapy`, Python 3.9), Unity 6000.5 with
Android Build Support. The code stays Python 3.9-compatible.

```powershell
.\artpipe.cmd py -m pip install --user -r requirements-maya.txt   # once: pyyaml, pytest, jsonschema
.\artpipe.cmd unity setup                                           # once: URP, Linear, text assets
.\artpipe.cmd fixtures                                              # build 45 test scenes into out\fixtures
.\artpipe.cmd build out\fixtures\SM_Messy.ma --class prop_small     # Maya -> Unity prefab
.\artpipe.cmd test                                                  # ~750 tests, ~5 min (close the Unity Editor first)
.\artpipe.cmd test -m "not maya and not unity and not comfy"        # pure Python only, ~3 s, any Python
```

`artpipe.cmd` finds `mayapy` (`%MAYAPY%` or the newest Maya). The Unity
runner uses the exact version pinned in `unity/ProjectSettings` (or `%UNITY%`).

| Command | Does |
|---|---|
| `artpipe generate "SUBJECT" --class C --style S --name N` | prompt to prefab: concept image, image-to-3D (both with the repair loop), ingest, then `artpipe build`; the build report carries the generation provenance (attempts, seeds, manifests, model licences, GPU) |
| `artpipe build SCENE --class C [--texture T ...]` | the whole chain: Maya validate/fix/export, then Unity import (creates the shared `M_<Name>.mat` from `T_<Name>_D/N` when missing); one build report |
| `artpipe validate FILE... --class C [--fix] [--export DIR]` | Maya stage only: validate (and fix) `.ma` / `.mb` / `.fbx`; `--export` writes each passing asset as canonical FBX plus its linked report |
| `artpipe unity import --class C FILE...` | Unity stage only: gate each FBX before it enters `Assets/Art/C/`, import with the class rules, measure, create the Prefab Variant |
| `artpipe unity verify` | re-check every asset under `Assets/Art` from the files, with no import cache |
| `artpipe unity setup\|test` | one-time project setup; Unity EditMode tests |
| `artpipe gen run WORKFLOW [--param NAME VALUE ...]` | generation stage: run a workflow from `gen/workflows/` on the pinned, headless ComfyUI; outputs plus a provenance manifest in `out/gen/runs/<run_id>/` |
| `artpipe gen concept "SUBJECT" --class C --style S [--seed N] [--repair]` | concept image (Z-Image-Turbo, prompt from the game's style in `conventions/gen_styles.yaml`) plus its foreground mask, checked for image-to-3D: plain background, coverage, margin, one object |
| `artpipe gen model CONCEPT_RUN --class C [--seed N] [--repair]` | image to textured low-poly GLB at the class budgets (TRELLIS.2), checked: budget, closed, manifold, UVs, UV overlap, hidden inner shells. `--repair` (also on `concept`): retry failed checks with the next seed per the policy in `generation.yaml`, at most 3 attempts, every attempt in `out/gen/repairs/` |
| `artpipe gen ingest MODEL_RUN --class C --name N` | GLB to a Maya scene (`SM_N.ma` + `T_N_D/N.png`), sized and placed for the class; then `artpipe build` as for any asset |
| `artpipe texture FILE... [--class C]` | the Unity texture rules in Python, before a texture reaches Unity (same check ids) |
| `artpipe gen server start\|stop\|status` | the pipeline's own ComfyUI server (keep it up for a batch: startup is ~15 s) |
| `artpipe conventions --dump\|--check` | write / verify `conventions/conventions.lock.json` |
| `artpipe docs [--check]` | regenerate the tables in the docs |
| `artpipe fixtures [DIR]` / `artpipe unity-fixtures` | build the broken test scenes / the golden FBX + reports the Unity tests import |
| `artpipe test [-m "not maya and not unity and not comfy"]` | the suite (Python, Maya, real Unity and ComfyUI runs) |

## Layout

```
conventions/                 the rules and the report contracts (YAML / JSON Schema)
validate/core/               pure Python: rule loader, lock, report types, FBX + .ma canonicalizers
validate/maya/               headless Maya: CLI, session, checks/, fixes/, FBX export
gen/                         generation: core/ (pure: rules, workflows, ComfyUI replies, manifest), comfy.py (server + HTTP), cli.py, workflows/
pipeline/                    unity.py (batchmode runner, exit-code reconciliation), build.py (end to end)
unity/                       Unity 6 URP project (Assets/Art is where imported assets go)
unity/Packages/com.artpipe.importer/
  Editor/                    gate, ModelImporter/TextureImporter rules, GPU checks, batchmode CLI
  Tests/Editor/              EditMode tests + Fixtures~ (golden FBX + reports)
tests/                       pure-Python tests, integration/ (mayapy), unity_editor/ (real Unity runs), generation/ (real ComfyUI)
docs/                        conventions.md, unity_import.md, generation.md, backlog.md
```

## Design choices

- **Built to be called by an agent.** Each stage is a CLI with stable check
  ids, JSON output, no UI, and one exit-code contract. Messages say what to
  do next ("move it to Assets/Art/prop_small/ or re-validate with --class
  pickup"). Exit 1 always means "fix the asset". Crashes, licence problems
  and a locked project are 2, never 1.
- **Deterministic end to end.** Same input, same bytes: reports, fixed `.ma`
  files, exported FBX, `.meta` GUIDs and new prefabs. Reports are diffable
  and cacheable by hash, and test fixtures can be committed as golden files.
- **Rules as data, enforced at every boundary.** One YAML source, resolved
  once by Python into a hash-linked lock that C# reads strictly. A stale lock
  stops a Unity import. An FBX enters the project only with a passing report
  for its exact bytes.
- **Measure what ships.** Vertex budgets are checked after the Unity import,
  where UV seams and hard edges have already split vertices, and texture
  formats are checked on the imported result. Both caught real problems
  that Maya-side checks can't see.
- **Probe before assuming.** Every Unity and Maya behaviour the design
  depends on was measured first, and the surprises are written down in the docs.
- **Proven by tests.** Every check fails on a deliberately broken input, with
  boundary cases. The suite was mutation-tested by hand: bugs injected on
  purpose must turn it red.

Known gaps and deferred work are tracked in [docs/backlog.md](docs/backlog.md).

## Roadmap

1. **Conventions + Maya validators**: done
2. **Unity import pipeline**: done (gate, import rules, GPU budgets, textures, batchmode, end to end)
3. **AI generation stage**: done. Text to concept image to TRELLIS.2 mesh to Maya to Unity works end to end in one command (`artpipe generate`), with a bounded repair loop and the provenance in the build report
4. Animated store banners at scale: one template → many games
5. MCP agent layer + docs + demo video
