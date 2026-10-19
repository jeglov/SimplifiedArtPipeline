# artpipe

An agent-runnable art pipeline for live mobile games. Every step runs
headless and returns machine-readable JSON, so an AI agent can do the whole
loop with no human in it: generate an asset, validate it, fix what is safe
to fix, and import it into Unity.

> **Status: Phase 1 complete.** Conventions, a headless Maya validator, safe
> auto-fixes and a Maya integration suite are done. Next: the Unity import
> pipeline (Phase 2). See the [roadmap](#roadmap).

## What it does today

```powershell
.\artpipe.cmd validate SM_Crate.ma --class prop_small --fix
```

1. **Opens** the file in Maya without a UI (`mayapy`). Script nodes are
   never executed.
2. **Runs 17 checks** against the rules for that asset class: topology,
   triangle, LOD and material budgets, UVs, transforms, pivot, size, naming,
   history.
3. **Applies only safe fixes** (7 of them): bakes history, freezes
   transforms, puts the pivot on the class anchor, renames default names,
   and more. A fix refuses, with a reason, when it can't be sure: negative
   scale, skinned meshes, a valid but conflicting name.
4. **Saves a fixed copy, re-opens it and validates it from scratch.** The
   source is never modified.
5. **Writes a deterministic JSON report** (same input, same bytes) with
   exact offenders (`|SM_Crate.f[12]`) and exit code `0` pass / `1` asset
   failed / `2` tool error.

Example: a rushed export with six problems (default names, foreign UV set
name, live history, unfrozen and off-centre transform, metre scene). All six
are fixed in one run, and the re-validated file **passes**. Holes,
non-manifold geometry, n-gons and over-budget meshes are never "fixed": the
report says where they are, and a human decides.

## The rules

All rules live in machine-readable files in [`conventions/`](conventions/),
not in code:

| File | Holds |
|---|---|
| [`mesh_budgets.yaml`](conventions/mesh_budgets.yaml) | 8 asset classes for low/mid Android: triangle, LOD and material budgets, size, pivot mode, UV rules, severity overrides |
| [`naming.schema.json`](conventions/naming.schema.json) | the naming grammar `<SM\|SK>_<AssetName>[_Variant][_LODn]`, with valid and invalid examples that the tests enforce |
| [`texture_rules.yaml`](conventions/texture_rules.yaml) | texture formats, map types and Unity import hints for Phase 2 |

**[docs/conventions.md](docs/conventions.md)** explains every rule and why
it exists, for artists and reviewers. Its tables are generated from those
files and from the code, and a test fails if the doc drifts.

## Quick start

Requirements: Windows, Maya 2023+ (`mayapy`, Python 3.9; the code stays
3.9-compatible).

```powershell
.\artpipe.cmd py -m pip install --user -r requirements-maya.txt   # once: pyyaml, pytest
.\artpipe.cmd fixtures                                              # build 45 test scenes into out\fixtures
.\artpipe.cmd validate out\fixtures\SM_Messy.ma --class prop_small --fix
.\artpipe.cmd test                                                  # 226 tests, ~15 s, one Maya boot
```

`artpipe.cmd` finds `mayapy` itself: `%MAYAPY%` if set, otherwise the newest
installed Maya.

| Command | Does |
|---|---|
| `artpipe validate FILE... --class C [--fix] [--max-offenders N]` | validate (and fix) one or more `.ma` / `.mb` / `.fbx` files |
| `artpipe fixtures [DIR]` | build the deliberately broken test scenes |
| `artpipe test [-m "not maya"]` | full suite, or the pure-Python core only (<1 s, no Maya needed) |
| `artpipe docs [--check]` | regenerate the tables in `docs/conventions.md` |

## Layout

```
conventions/            the rules (YAML / JSON Schema), the single source of truth
validate/core/          pure Python, never imports Maya: rule loader, report types, paths
validate/maya/checks/   one module per check, same contract: run(ctx) -> CheckResult
validate/maya/fixes/    one module per safe fix: blocker(ctx, result) + apply(ctx, result)
validate/maya/          CLI, Maya session, scene context, registry (explicit check/fix lists)
tests/                  core unit tests (any Python) + tests/integration/ (mayapy)
tests/fixtures/         code that builds 45 broken scenes + what each must fail before/after --fix
docs/                   conventions.md, backlog.md (deferred issues, tagged by phase)
```

## Design choices

- **Built to be called by an agent.** Each check and fix is a small module
  with a fixed interface and a stable id (`topology.ngons`). Output is JSON
  with no UI. Messages say how far over a limit an asset is, or which unit
  it was probably authored in, so the agent knows what to try next.
- **Deterministic.** Sorted offenders, rounded floats, no timestamps,
  repo-relative paths, and Maya's per-save UUIDs stripped from fixed `.ma`
  files. A report is diffable and cacheable by hash.
- **Safe auto-fixing.** A fix runs only if it is deterministic, the
  visible result doesn't change (or the convention defines the change), and
  no blocker applies. `fixable: true` in a report means the fix would really
  run on that failure.
- **Proven by tests.** Every check fails on at least one deliberately broken
  scene, and every scene fails *exactly* its listed checks (no false
  positives) before and after `--fix`. The suite was mutation-tested by
  hand: bugs injected on purpose must turn it red.

Known gaps and deferred work are tracked in [docs/backlog.md](docs/backlog.md).

## Roadmap

1. **Conventions + Maya validators**: done
2. Unity import pipeline: AssetPostprocessors, presets, batchmode
3. AI generation stage: ComfyUI, image → 3D
4. Animated store banners at scale: one template → many games
5. MCP agent layer + docs + demo video
