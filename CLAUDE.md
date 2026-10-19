# CLAUDE.md — artpipe

## Context
- Portfolio project for a freelance **Technical Artist (AI)** role at **Voodoo** (Portfolio Games Team, Paris).
- Voodoo runs 40+ live mobile games and is building **"Sophia"**, an in-house AI agent that automates game ops.
- Goal: art production that agents can run. Assets get generated, validated, fixed and imported into Unity with minimal human intervention.
- `artpipe` is a mini "Sophia art pipeline" built to show that.

## Roadmap
1. Conventions + Maya validators (done)
2. **Unity import pipeline** (AssetPostprocessors, presets, batchmode) ← current phase
3. AI generation stage (ComfyUI, image→3D)
4. Animated store banners at scale (one template → many games)
5. MCP agent layer + docs + demo video

Deferred issues are tracked in [docs/backlog.md](docs/backlog.md), tagged by stage (P1.3 … P5). Add new deferred issues there. Don't silently drop them.

## Phase 2 deliverables (in this order, split to be confirmed with the user)
1. Unity project skeleton in `unity/`: URP, embedded package `com.artpipe.importer` with an Editor-only `.asmdef`, gitignore and `.gitattributes` rules for Unity
2. `conventions.lock.json` (deterministic dump of `conventions/`) plus a typed C# loader (Newtonsoft). No budget numbers in C#. Stale-lock test (backlog #10)
3. FBX export from Maya with explicit, versioned exporter settings. The Maya report sits next to the FBX, linked by sha256 (backlog #27)
4. Import gate: an `AssetPostprocessor` refuses or flags an FBX whose Maya report is missing, failed or hash-mismatched
5. Model import: `ModelImporter` settings per class, LODGroup from `_LODn`, post-import vertex/submesh/LOD-ratio budgets (backlog #11)
6. Textures driven by `texture_rules.yaml`: ASTC per platform, sRGB/linear, max size, POT, alpha, names (backlogs #14, #22)
7. Batchmode CLI `-executeMethod Artpipe.Cli.Import`: exit codes 0/1/2 and a deterministic JSON report
8. End to end: Maya validate/fix → FBX → Unity batch import → combined report. A messy fixture comes out as a clean prefab
9. Unity Test Framework EditMode tests fed by Phase 1 fixtures, `docs/unity_import.md`, README update

Phase 1 (conventions, 17 checks, 7 fixes, 226 tests, generated docs) is described in README.md and docs/conventions.md.

## Architecture rules (non-negotiable)
- Each check is a small, independent module, and all checks share the same interface:
  - input: mesh/scene
  - output: `{id, passed, severity, metrics, message, fixable}`
- An AI agent will call checks and fixes as tools later, so outputs must be **deterministic**, **machine-readable** (JSON) and need **no UI**.
- Code must stay compatible with Python 3.9. Keep pure-Python logic in `validate/core/` so it can be tested without Maya.
- Rules come only from `conventions/`. C# reads `conventions.lock.json` and never duplicates numbers.
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
- **End every reply with a running Phase 2 progress checklist.**
