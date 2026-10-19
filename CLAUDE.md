# CLAUDE.md — artpipe

## Context
- Portfolio project for a freelance **Technical Artist (AI)** role at **Voodoo** (Portfolio Games Team, Paris).
- Voodoo runs 40+ live mobile games and is building **"Sophia"**, an in-house AI agent that automates game ops.
- Goal: art production that agents can run. Assets get generated, validated, fixed and imported into Unity with minimal human intervention.
- `artpipe` is a mini "Sophia art pipeline" built to show that.

## Roadmap
1. **Conventions + Maya validators** ← current phase
2. Unity import pipeline (AssetPostprocessors, presets, batchmode)
3. AI generation stage (ComfyUI, image→3D)
4. Animated store banners at scale (one template → many games)
5. MCP agent layer + docs + demo video

Deferred issues are tracked in [docs/backlog.md](docs/backlog.md), tagged by stage (P1.3 … P5). Add new deferred issues there. Don't silently drop them.

## Phase 1 deliverables (in this order)
1. Repo skeleton: `conventions/`, `validate/maya/checks/`, `validate/maya/fixes/`, `docs/`, `tests/`
2. Machine-readable conventions: `naming.schema.json`, `mesh_budgets.yaml` (mobile tri budgets per asset class, LOD ratios, UV rules), `texture_rules.yaml`
3. Headless Maya validator run with `mayapy` from the CLI. It loads an FBX/MA file and outputs a JSON report. Checks: non-manifold geometry, lamina faces, open borders (watertight), ngons, tri count vs budget, UVs (present / inside 0–1 / overlapping), frozen transforms, pivot location, scale, naming convention, construction history
4. Auto-fixes for every check where a fix is safe, followed by re-validation
5. A test set of deliberately broken meshes, plus a test runner that proves each check catches its case
6. `docs/conventions.md` and a short README section

## Architecture rules (non-negotiable)
- Each check is a small, independent module, and all checks share the same interface:
  - input: mesh/scene
  - output: `{id, passed, severity, metrics, message, fixable}`
- An AI agent will call checks and fixes as tools later, so outputs must be **deterministic**, **machine-readable** (JSON) and need **no UI**.
- Code must stay compatible with Python 3.9. Keep pure-Python logic in `validate/core/` so it can be tested without Maya.

## Environment
- Windows 11, Maya 2023 (`mayapy`, Python 3.9)
- mayapy: `C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe`, found automatically by `artpipe.cmd` (or set `MAYAPY`)
- Run the tests: `.\artpipe.cmd test`. Validate: `.\artpipe.cmd validate FILE --class prop_small`. Raw mayapy: `.\artpipe.cmd py ...`
- The user works both locally and in Claude Code.

## About the user
- Technical artist with a strong Unreal background (Blueprints, materials), hard-surface modeling, Maya and Houdini.
- Has mobile game production and monetization/UA experience.
- Builds with Claude Code and MCP agents on their own Godot/C# game.
- **Less experienced with:** Maya Python tooling run headless through mayapy, and Unity C# editor tooling. Explain these areas more carefully.

## How to work with the user
- **Priority 1: practical build guidance.** Go step by step and give concrete code, commands and file contents. Say exactly what to run and what output to expect.
- **Priority 2: theory.** Explain the "why" briefly (mesh topology, mobile budgets, Maya API choices such as `cmds` vs OpenMaya 2.0), and only where it helps a decision or an interview answer.
- **One step at a time.** After each step, stop and wait for the user to confirm it works or to paste errors.
- **Ask first** for anything you don't know. Don't guess about the environment.
- **Flag anything a senior TA reviewer at a mobile studio would judge as amateur.**
- **End every reply with a running Phase 1 progress checklist.**
