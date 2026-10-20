# CLAUDE.md — artpipe

## Context
- Portfolio project for a freelance **Technical Artist (AI)** role at **Voodoo** (Portfolio Games Team, Paris).
- Voodoo runs 40+ live mobile games and is building **"Sophia"**, an in-house AI agent that automates game ops.
- Goal: art production that agents can run. Assets get generated, validated, fixed and imported into Unity with minimal human intervention.
- `artpipe` is a mini "Sophia art pipeline" built to show that.

## Roadmap
1. Conventions + Maya validators (done)
2. Unity import pipeline (done)
3. **AI generation stage** (ComfyUI, image→3D) ← **current phase** (agreed 2026-10-20)
   - Decisions: local only, no paid API. Image model **Z-Image-Turbo fp8** (Apache 2.0). Image→3D **TRELLIS.2** native in ComfyUI (MIT), with the 8 GB VRAM fit probed first and Comfy Cloud as the fallback. Hunyuan3D is ruled out (its licence excludes the EU/UK). Target classes: `pickup`, `prop_small`. ComfyUI runs headless and pinned (auto-update off, bundled Python 3.13 on 127.0.0.1, started by the pipeline). Texel density (#13): build it as a warning, measure on generated assets, then pin per-class targets. Skinning (#12) stays deferred.
   - Steps: 3.1 pin + probe ComfyUI API (docs/generation.md) · 3.2 generation harness + provenance manifest · 3.3 concept image + style template + image pre-checks (#40) · 3.4 image→3D (VRAM and output-format probe) · 3.5 Maya ingest: GLB reader, unit/axis (#4), decimate to budget, texture resize, per-shell winding (#43), texel density (#13) · 3.6 bounded repair loop (policy in conventions/) · 3.7 `artpipe generate` end to end, provenance in the build report · 3.8 tests (recorded generator outputs as fixtures) + docs
   - Generation never writes into the Unity project: generated assets go through the same validate → export → gate path. The model is the only nondeterministic step: record GPU/driver/torch/ComfyUI/model hashes, and keep everything downstream deterministic.
4. Animated store banners at scale (one template → many games)
5. MCP agent layer + docs + demo video

Deferred issues are tracked in [docs/backlog.md](docs/backlog.md), tagged by stage (P1.3 … P5). Add new deferred issues there. Don't silently drop them.

## What exists (Phases 1 and 2)
- Phase 1: conventions, headless Maya validator (18 checks, 8 safe fixes), deterministic reports. See docs/conventions.md.
- Phase 2: canonical FBX export, Unity 6 URP project with the `com.artpipe.importer` package (import gate, per-class ModelImporter/TextureImporter rules, GPU vertex budgets, Prefab Variants), batchmode `artpipe unity import|verify`, and `artpipe build` (Maya scene to Unity prefab). See docs/unity_import.md and README.md.
- Report contracts: `conventions/*.schema.json`. Rules reach C# only through `conventions.lock.json` (`artpipe conventions --dump`).
- Phase 3 so far: the `gen/` harness. `artpipe gen run WORKFLOW` drives the pinned ComfyUI headless (its own server; it refuses the Desktop app's), checks model pins by sha256, reuses runs by run id, and writes `out/gen/runs/<run_id>/manifest.json` (`conventions/gen_manifest.schema.json`). Rules: `conventions/generation.yaml`. Probed behaviours: docs/generation.md.
- `artpipe gen concept "SUBJECT" --class C --style S`: Z-Image-Turbo int8 concept image + BiRefNet mask, prompt from `conventions/gen_styles.yaml`, `concept.*` checks on the mask (the mask image-to-3D uses). `artpipe texture`: Unity's texture rules in pure Python (`validate/core/image.py` reads PNG/TGA without Pillow).
- `artpipe gen model` (TRELLIS.2, decimate to 0.9 x tris_max, bake D/N at the class texture size, `model.*` GLB checks) and `artpipe gen ingest` (GLB to `out/gen/assets/<Name>/SM_<Name>.ma`), then the normal `artpipe build`. New Maya checks: `topology.winding` (#43), `uv.texel_density` (#13). `artpipe generate "SUBJECT" --class C --style S --name N` (`pipeline/generate.py`): concept -> model (repair loop) -> ingest -> build, `generation` block in the build report (schema 1.1); `artpipe unity import` creates `M_<Name>.mat` from `T_<Name>_D/N` (`SharedMaterials.cs`, #49). Repair loop: `gen concept|model --repair` (`gen/core/repair.py`, policy `repair:` in generation.yaml, records in `out/gen/repairs/`, `conventions/gen_repair.schema.json`). Keep `unity/Assets/Art` empty when running tests (#52).
- Models live in `%LOCALAPPDATA%\Comfy-Desktop\ComfyUI-Shared\models`, pinned by sha256 in `generation.yaml` (~21 GB downloaded: Z-Image, Qwen3, VAE, BiRefNet, TRELLIS.2, DINOv3). ComfyUI's Python (with Pillow) is `...\ComfyUI\.venv\Scripts\python.exe`, handy for contact sheets.
- Tests: `artpipe test` (~750, ~5 min, close the Unity Editor first); fast run `-m "not maya and not unity and not comfy"`. Real ComfyUI replies are recorded in `tests/fixtures/comfy/` (`artpipe py tests/fixtures/comfy_record.py`).

## Architecture rules (non-negotiable)
- Each check is a small, independent module, and all checks share the same interface:
  - input: mesh/scene
  - output: `{id, passed, severity, metrics, message, fixable}`
- An AI agent will call checks and fixes as tools later, so outputs must be **deterministic**, **machine-readable** (JSON) and need **no UI**.
- Code must stay compatible with Python 3.9. Keep pure-Python logic in `validate/core/` so it can be tested without Maya.
- Rules come only from `conventions/`. C# reads `conventions.lock.json` and never duplicates numbers.
- Every Unity or Maya behaviour the design depends on is probed first and recorded (docs/unity_import.md lists the Unity ones).
- Unity importers must be idempotent (re-import changes nothing) and deterministic despite Unity's own nondeterminism (`.meta` GUIDs, import order).
- Every check and fix is proven by a test, including a boundary case. Mutation-test the important ones.

## Environment
- Windows 11, Maya 2023 (`mayapy`, Python 3.9)
- mayapy: `C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe`, found automatically by `artpipe.cmd` (or set `MAYAPY`)
- Run the tests: `.\artpipe.cmd test`. Validate: `.\artpipe.cmd validate FILE --class prop_small`. Raw mayapy: `.\artpipe.cmd py ...`
- Unity 6000.5.8f1 (Unity Personal, works in batchmode): `C:\Program Files\Unity\Hub\Editor\6000.5.8f1\Editor\Unity.exe`. Modules: Android (SDK, NDK, OpenJDK), Windows, WebGL. No iOS module
- IDE: Visual Studio Community 2022 with the "Game development with Unity" workload
- The user works both locally and in Claude Code.

## About the user
- Technical artist with a strong Unreal background (Blueprints, materials), hard-surface modeling, Maya and Houdini.
- Has mobile game production and monetization/UA experience.
- Builds with Claude Code and MCP agents on their own Godot/C# game.
- **Less experienced with:** Unity C# editor tooling (AssetPostprocessor, ModelImporter/TextureImporter, asmdefs, batchmode, Unity Test Framework) and, earlier, headless mayapy. Explain these carefully, like to a junior tech artist when the user says they're lost.

## How to work with the user
- **Priority 1: practical build guidance.** Go step by step and give concrete code, commands and file contents. Say exactly what to run and what output to expect.
- **Priority 2: theory.** Explain the "why" briefly (mesh topology, mobile budgets, Maya API choices such as `cmds` vs OpenMaya 2.0), and only where it helps a decision or an interview answer.
- **One step at a time.** After each step, stop and wait for the user to confirm it works or to paste errors.
- **Ask first** for anything you don't know. Don't guess about the environment.
- **Create files yourself** by default and explain each one. If the user wants to type it, give full contents and exact commands.
- **Probe before assuming** an API's behaviour: small experiment in Unity or Maya first.
- **Test the tests:** inject a bug on purpose and check the suite goes red.
- **Commits** only when the user says "commit". Conventional Commits (`feat(unity): …`), body explains the why, every commit runnable.
- **Reviewer questions:** at the end of every step, append numbered questions to `notes/reviewer_questions.md` (format: "Answer so far" + "Dig deeper") and say which numbers were added.
- **Flag anything a senior TA reviewer at a mobile studio would judge as amateur.**
- **End every reply with a running progress checklist for the current phase.**
