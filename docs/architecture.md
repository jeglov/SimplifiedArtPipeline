# artpipe architecture (one page)

For a reviewer who has ten minutes. The details are in the stage docs linked
at the end.

## The idea

Art production that an agent can run. Every stage is a headless CLI that
reads its rules from `conventions/`, writes a deterministic JSON report and
returns one exit code: 0 pass, 1 "the asset needs fixing", 2 tool error. An
agent (Claude Code today, an in-house agent like Sophia later) drives those
stages through an MCP server, makes the judgement calls, and asks a human
before anything reaches the game.

```
                 conventions/  (YAML rules + JSON Schemas; one hash-linked lock for C#)
                      |
   prompt --> [gen] concept image --> image-to-3D GLB --> Maya scene        (ComfyUI, local, pinned models)
                      |                                       |
                      |                     [validate] Maya checks + safe fixes --> canonical FBX + report
                      |                                       |
                      |                     [unity] import gate --> import rules --> GPU checks --> prefab
                      |
   kit + template --> [banners] HTML page --> Chromium frames --> MP4 --> 17 checks + repair --> batch

   MCP client (the agent) <--stdio--> agent/server.py --> artpipe.cmd ... (one process per call, or a job)
                                          |
                                          +-- jobs (GPU FIFO), Maya worker, approval queue, reviews
```

## Layers

| Layer | Runs in | Decides |
|---|---|---|
| Rules (`conventions/`) | data | every number: budgets, texture sizes, network specs, gated actions |
| Checks and fixes (`validate/`, `gen/core/`, `banners/core/`) | Python 3.9 (mayapy for Maya) | pass/fail, deterministically; each check is one module with the same output `{id, passed, severity, metrics, message, fixable}` |
| Stage CLIs (`artpipe validate`, `unity import`, `gen`, `banners`, `build`, `generate`) | `artpipe.cmd` | the order of checks, repair loops with fixed attempt budgets |
| Unity package (`com.artpipe.importer`) | Unity 6 batchmode | the import gate (no passing report for these exact bytes, no import), import settings, measured GPU budgets |
| Agent layer (`agent/`) | Python 3.12 venv, official MCP SDK | nothing about the asset: it runs the CLIs, queues jobs, bounds retries, and refuses what isn't allowed |
| The agent (the MCP client) | Claude Code / Sophia | taste (is it the right object? would a UA lead ship it?), what to try next, when to ask a human |

## Where the non-determinism is, and how it's fenced

- **The image and 3D models** are the only nondeterministic steps. Every run
  records GPU, driver, torch, ComfyUI and model sha256s in a provenance
  manifest; everything downstream is byte-deterministic (reports, `.ma`,
  FBX, `.meta` GUIDs, banner MP4s on this machine).
- **The agent's judgement** never gates anything. A review is stored next to
  the deterministic reports as evidence; a new prompt is budgeted (2 per
  original concept) and recorded with its reason.
- **Unity's own nondeterminism** (GUIDs, import order) is neutralised by the
  importer: GUIDs derive from paths, sidecars import before their FBX, and
  re-import changes nothing.

## Control: what the agent may do on its own

| Free | Through the approval queue (a human runs `artpipe queue approve`) |
|---|---|
| anything that writes under `out/`: validate, fix into `out/`, generate concepts and models, render and check banners, `unity verify` (read-only) | `build` and `generate` (they import into `unity/Assets`), kit art (overwrites a game's files), golden re-records |

The queue stores the exact command and its sha256; an approval runs once and
only for that command. There is no approve tool. Claude Code permission
rules deny file edits under `out/agent/queue/`; a shell could still write
there, so team use needs signed decisions (backlog #65, #38).

## Long work

GPU work (ComfyUI generation, banner art) runs as a job: a detached runner
process, `job_status` with live progress parsed from ComfyUI's log, FIFO
behind a GPU lock so two agents don't fight over 8 GB of VRAM. ComfyUI is
shared through registered sessions: the last user out stops the server the
pipeline started. Maya stays loaded in a worker process (localhost, token)
so a check costs 0.04-0.12 s instead of a 4.6 s cold start.

## What I'd change for a studio

- CI on a licensed runner for Maya and Unity tests (#16, #31).
- Signed reports and signed approvals (#38, #65).
- A persistent Unity worker and a cached `Library/` (#30).
- Per-game style references (LoRA or image prompts) so 40 games don't
  drift on prompt-level consistency (#47).

## Further reading

[conventions.md](conventions.md) (rules, Maya checks) ·
[unity_import.md](unity_import.md) (gate, import rules, probed Unity behaviour) ·
[generation.md](generation.md) (ComfyUI, models, repair loop) ·
[banners.md](banners.md) (UA creatives) ·
[agent.md](agent.md) (the MCP layer, example sessions) ·
[agent_tools.md](agent_tools.md) (generated tool reference) ·
[backlog.md](backlog.md) (deferred work)
