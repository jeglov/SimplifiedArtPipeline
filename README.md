# artpipe

**An art pipeline for mobile games that an AI agent can run on its own.**

artpipe automates the repetitive work in a mobile studio's asset pipeline.
It creates a 3D asset from a text prompt, checks it against the studio's
rules, fixes what is safe to fix, and imports it into Unity as a
ready-to-use prefab. It also turns one ad template into checked video ads
for many games. Every step runs without a UI and reports its result as
JSON, so an AI agent (Claude Code today, an in-house agent later) can drive
the whole loop. A person approves only the steps that touch the actual game.

> **Status:** portfolio project, limited to use in an actual production enviroment. Technical overview:
> [docs/architecture.md](docs/architecture.md).

## What it solves

| Problem | What artpipe does |
|---|---|
| Exports arrive with small mistakes: default names, wrong scale, leftover history, bad pivot | Checks every asset against written rules (18 checks), applies the safe fixes, and reports what a person must fix |
| Budgets break unnoticed: triangles, GPU vertices, texture size and format | Measures the asset after the Unity import, where UV seams have already split vertices |
| Every game imports assets slightly differently | One Unity import gate: an asset gets in only with a passing report for its exact bytes, with its class's import settings |
| Props and pickups for content updates cost artist time | Prompt to prefab in one command, through the same checks as hand-made art. Local models with commercial-use licences only |
| UA needs many videos: per game, placement and language | One template plus a kit per game gives a 9:16 video, checked against Meta, TikTok, Unity Ads and AppLovin specs and repaired automatically |
| An agent shouldn't write into a live game unsupervised | An MCP server with safe tools. Anything that writes into the game waits in an approval queue |

## Limitations

A working proof on one machine, not yet a tool another studio can clone and run.

- **One machine, one toolchain.** Tested on Windows 11, Maya 2023, Unity 6000.5 and one pinned ComfyUI only. Never set up on a clean machine.
- **Manual setup.** Several setup commands, no single setup check. Generation needs an NVIDIA GPU with 8 GB+ VRAM and ~21 GB of models.
- **Simple static props only.** Classes `pickup` and `prop_small`; no characters, rigs or animation. About half of image-to-3D attempts fail first try; retries only change the seed.
- **No likeness check.** A generated model can pass every check and still lose the concept's key detail. The agent reviews 2D images, never the 3D model.
- **Silent 9:16 video ads only.** No playables, audio or other formats. Demo: one made-up game, English.
- **Budgets are defaults**, not profiled on a low-end Android device.
- **No CI.** Maya and Unity need licences a hosted runner doesn't have.
- **Local trust.** Reports and approvals aren't signed. An agent can't approve its own work through its tools, but someone with shell access could.

Every gap, with a plan: [docs/backlog.md](docs/backlog.md).

## How it works

### Maya scene to Unity prefab

```powershell
.\artpipe.cmd build out\fixtures\SM_Messy.ma --class prop_small --texture T_Messy_D.png
# artpipe build: PASS out/build/SM_Messy/build.report.json -> Assets/Art/prop_small/SM_Messy.prefab
```
# Example asset:
`SM_Messy` has six problems: default names, a wrong UV set name, live
history, unfrozen transforms, an off-centre pivot, a metre scene. In ~20 s:

1. **Maya (headless)** checks it against its class rules, applies the safe fixes, and re-checks the fixed copy. Holes, n-gons and over-budget meshes are never auto-fixed: the build stops and the report says where.
2. **FBX export** with fixed, verified settings. Same scene, same bytes. The FBX is checked again.
3. **Unity (batchmode)** accepts the FBX only with a passing report for those bytes, imports it with the class settings, measures what the GPU gets, compresses the texture to ASTC and creates the prefab.
4. **One build report** links every stage by path and sha256. Exit codes everywhere: `0` pass, `1` fix the asset, `2` the tool broke.

### Prompt to prefab

```powershell
.\artpipe.cmd generate "a golden coin with a star emblem" --class pickup --style casual_toon --name Coin
# artpipe generate: PASS out/build/SM_Coin/build.report.json -> Assets/Art/pickup/SM_Coin.prefab
```

1. **Concept image** (Z-Image-Turbo) in the game's style, cut out with a mask, checked for 3D use: one object, plain background, margin.
2. **3D model** (TRELLIS.2): textured, low-poly, within budget; checked for closed shape, clean UVs, no hidden inner parts.
3. **Retry** on a failed check with a new seed, at most 3 attempts, all recorded.
4. **Same build** as a hand-made asset. No shortcut.

The report records the prompt, seeds, model licences and checksums, GPU and
software versions. The AI model is the only nondeterministic step.
Details: [docs/generation.md](docs/generation.md).

### One template to many video ads

```powershell
.\artpipe.cmd banners setup                                          # once: pinned ffmpeg + Chromium + Playwright
.\artpipe.cmd banners batch --template hero_endcard --games spotcap_grove
# artpipe banners: 1 banner(s): 1 passed, 0 handed over, 0 errors, 0 skipped, 0 reused, 1 repaired, 322 s.
```

![Spotcap Grove, hero_endcard, 9:16: frames at 0, 1, 3, 5, 6 and 8 s](docs/images/spotcap_grove_hero_endcard_sheet.png)

*Spotcap Grove is a made-up game. Hero: a generated concept, cut out.
Background: one generated image. Logo and text: open-licence fonts. AI never
makes text or logos.*

1. **Template + kit.** The template holds layers, keyframes and slots; each game's kit fills them (palette, fonts, art, text). Every kit file has a licence and provenance record. Kit checks run first.
2. **Render** frame by frame in headless Chromium, then a pinned x264 encode. Byte-identical re-renders on this machine.
3. **17 checks** on the decoded video. Network specs: size, codec, frame rate, length, file size, bitrate, safe zone. House rules: text fit, font fallback, text size, contrast on the real pixels, first frame, end card, logo.
4. **Repair**: shrink text, recolour from the palette, or hand over with the reason. Here the headline wrapped to 3 lines; shrunk to 79 %, it passed on the second render.
5. **Batch**: game × placement × language, unchanged inputs reused (322 s rendered, 2 s reused), with a summary, contact sheet and a look-alike warning.

Details: [docs/banners.md](docs/banners.md).

### Agent layer

```powershell
.\artpipe.cmd agent setup        # once: Python 3.12 venv with the hash-locked MCP SDK
# open the repo in Claude Code: .mcp.json registers `artpipe agent serve`; /mcp lists 29 tools
.\artpipe.cmd queue list          # what the agent asked to do, and what it handed over
.\artpipe.cmd queue approve approval-1a2b3c4d5e --note "ok"   # at a console: you type the id back
```

1. **artpipe is the MCP server; the client is the agent.** Each tool runs one artpipe command. The pipeline never calls an LLM.
2. **Fine and coarse tools.** Fine: list checks, run one check, apply one fix, in a Maya process that stays open (~0.1 s instead of a 4.6 s start). Coarse: validate, generate, render ads, verify Unity.
3. **Jobs** for long work, with progress. GPU jobs run one at a time.
4. **Approval queue.** Anything that writes outside `out/` (Unity import, kit art, goldens) waits for a person to approve that exact command. Failed jobs land there with their reason.
5. **Review tools** show the agent's model images and metrics for what checks can't judge. It records a verdict and may reword a concept prompt at most twice.

Guide: [docs/agent.md](docs/agent.md). Tool reference (generated): [docs/agent_tools.md](docs/agent_tools.md).

## Rules

All rules are data in [`conventions/`](conventions/), never numbers in code.
Unity reads them from a generated, checksummed `conventions.lock.json`; a
stale lock blocks the import.

| File | Defines |
|---|---|
| [`mesh_budgets.yaml`](conventions/mesh_budgets.yaml) | 8 asset classes for low/mid Android: triangle and GPU vertex budgets, LODs, materials, size, pivot, UVs |
| [`naming.schema.json`](conventions/naming.schema.json) | naming `<SM\|SK>_<AssetName>[_Variant][_LODn]`, with examples both Python and C# tests use |
| [`texture_rules.yaml`](conventions/texture_rules.yaml) | map types (`_D _N _MS _AO _E`): colour space, alpha, compression per platform, max size per class |
| [`fbx_export.yaml`](conventions/fbx_export.yaml) | every FBX exporter setting, versioned |
| [`unity_import.yaml`](conventions/unity_import.yaml) | Unity folders, shared materials, LOD distances, import settings per class |
| [`generation.yaml`](conventions/generation.yaml), [`gen_styles.yaml`](conventions/gen_styles.yaml) | pinned AI models (sha256, licence), concept and model checks, retry policy, art styles |
| [`banners.yaml`](conventions/banners.yaml) | ad placements (each number cited), pinned render tools, encoder, allowed licences, house rules, repair policy |
| `*.schema.json` | report formats |

Rule docs: [docs/conventions.md](docs/conventions.md) (Maya side),
[docs/unity_import.md](docs/unity_import.md) (Unity side). Their tables are
generated from these files; tests fail if they drift.

## Getting started

**Requires:** Windows, Maya 2023+ (`mayapy`, Python 3.9), Unity 6000.5 with
Android Build Support. Generation: Comfy Desktop, NVIDIA GPU with 8 GB+
VRAM. Video ads: Node.js 20+.

```powershell
.\artpipe.cmd py -m pip install --user -r requirements-maya.txt   # once: pyyaml, pytest, jsonschema
.\artpipe.cmd unity setup                                           # once: URP, Linear, text assets
.\artpipe.cmd fixtures                                              # builds 45 broken test scenes into out\fixtures
.\artpipe.cmd build out\fixtures\SM_Messy.ma --class prop_small     # first Maya -> Unity build
.\artpipe.cmd banners setup                                          # once: pinned ffmpeg + Chromium for the ads
.\artpipe.cmd test                                                  # ~1100 tests, ~18 min (close the Unity Editor and ComfyUI first)
.\artpipe.cmd test -m "not maya and not unity and not comfy and not render"   # pure Python only, ~20 s
```

`artpipe.cmd` finds Maya (or `%MAYAPY%`) and the Unity version pinned in
`unity/ProjectSettings` (or `%UNITY%`).

## Commands

### 3D assets

| Command | Does |
|---|---|
| `artpipe build SCENE --class C [--texture T ...]` | Maya check, fix, export, then Unity import (creates `M_<Name>.mat` from `T_<Name>_D/N` if missing). One build report |
| `artpipe validate FILE... --class C [--fix] [--export DIR]` | Maya only: check (and fix) `.ma`, `.mb`, `.fbx`; `--export` writes clean FBX + report |
| `artpipe texture FILE... [--class C]` | Unity texture rules, checked in Python before Unity |
| `artpipe unity import --class C FILE...` | Unity only: gate, import with class rules, measure, create the prefab |
| `artpipe unity verify` | Re-check every asset in `Assets/Art` from the files |
| `artpipe unity setup\|test` | One-time project setup; Unity tests |

### AI generation

| Command | Does |
|---|---|
| `artpipe generate "SUBJECT" --class C --style S --name N` | Prompt to prefab, with provenance in the build report |
| `artpipe gen concept "SUBJECT" --class C --style S [--seed N] [--repair]` | Concept image + mask, checked for 3D use |
| `artpipe gen model CONCEPT_RUN --class C [--seed N] [--repair]` | Image to low-poly GLB within budget, checked. `--repair`: up to 3 seeds, recorded in `out/gen/repairs/` |
| `artpipe gen ingest MODEL_RUN --class C --name N` | GLB to a Maya scene, sized for the class, ready for `build` |
| `artpipe gen run WORKFLOW [--param NAME VALUE ...]` | Any workflow in `gen/workflows/` on the pinned ComfyUI, with a manifest |
| `artpipe gen server start\|stop\|status` | The pipeline's ComfyUI server (~15 s start; keep it up for batches) |

### Video ads

| Command | Does |
|---|---|
| `artpipe banners setup\|status` | Install / verify pinned ffmpeg, Chromium, Playwright |
| `artpipe banners kit KIT --template T` | Kit checks: slots, files, licences, colours, images, background |
| `artpipe banners art hero\|background\|logo KIT ...` | Kit art with provenance: hero cut-out, one generated background, logo |
| `artpipe banners render KIT --template T [--repair]` | One video + layout record + contact sheet + 17 checks; `--repair` fixes and re-renders |
| `artpipe banners check FOLDER` | Re-run checks on a rendered video |
| `artpipe banners batch --template T --games G1,G2` | Game × placement × language, with reuse; summary in `out/banners/batch/` |

### Agent and approvals

| Command | Does |
|---|---|
| `artpipe agent setup\|status\|serve\|docs [--check]` | MCP server: install, status, run (Claude Code uses `.mcp.json`), regenerate tool reference |
| `artpipe queue list\|show\|approve\|reject\|resolve` | The human side of the approval queue |

### Maintenance

| Command | Does |
|---|---|
| `artpipe conventions --dump\|--check` | Write / verify `conventions/conventions.lock.json` |
| `artpipe docs [--check]` | Regenerate doc tables |
| `artpipe fixtures [DIR]` / `artpipe unity-fixtures` | Build broken test scenes / reference FBX + reports for Unity tests |
| `artpipe test [-m "..."]` | Test suite: Python, Maya, Unity, ComfyUI, renders |

## Layout

```
conventions/      rules and report formats (YAML / JSON Schema)
validate/core/    pure Python, no Maya needed: rules, reports, FBX + .ma canonicalizers
validate/maya/    headless Maya: checks, fixes, FBX export
gen/              AI generation: ComfyUI server, workflows, manifests, retry loop
banners/          video ads: templates, kits, fonts, renderer (Node + Playwright), checks, repair, batch
agent/            MCP server: jobs, approval queue, review tools, path guard
pipeline/         Unity batchmode runner, end-to-end build
unity/            Unity 6 URP demo project (imports land in Assets/Art)
unity/Packages/com.artpipe.importer/
  Editor/         import gate, import rules, GPU checks, batchmode entry
  Tests/Editor/   Unity tests + reference FBX and reports
tests/            Python, Maya, real Unity and real ComfyUI tests
docs/             architecture, conventions, Unity import, generation, banners, agent, backlog
```

## Design choices

- **Agent first.** Stable check ids, JSON output, no UI, one exit-code contract. Messages say what to do next. `1` always means "fix the asset"; crashes, licence problems and a locked project are `2`.
- **Deterministic.** Same input, same bytes: reports, fixed `.ma`, FBX, `.meta` GUIDs, prefabs. Reports are diffable and cacheable by hash.
- **Rules as data, enforced at every hand-off.** One YAML source for Python and C#. An FBX enters the game only with a passing report for its exact bytes.
- **Measure what ships.** Vertex budgets and texture formats are checked after the Unity import. This caught real problems Maya can't see.
- **Probe before assuming.** Every Maya and Unity behaviour the design relies on was measured first and written up.
- **Tested tests.** Every check fails on a broken input, with boundary cases. Bugs injected on purpose (mutation testing) must turn the suite red.

## Roadmap

1. **Rules + Maya validator**: done
2. **Unity import pipeline**: done
3. **AI generation**: done. Prompt → concept → 3D → Maya → Unity, with retries and provenance
4. **Video ads at scale**: done. One template, a kit per game, checked against four networks, repaired, batched
5. **Agent layer**: done. 29 MCP tools, background jobs, GPU queue, persistent Maya worker, approval queue, review tools

Next: **plug & play**. One setup check, other Maya and Unity versions, a
studio's own projects, and a one-line `artpipe make "a gold coin"`. See
[docs/backlog.md](docs/backlog.md).
