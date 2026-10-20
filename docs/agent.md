# The MCP agent layer (Phase 5)

artpipe as tools an agent can call. The agent (Claude Code today, Sophia
later) is the MCP client and makes every judgement call; artpipe stays
deterministic and never calls an LLM. Scope agreed on 2026-10-20 (see
`CLAUDE.md`).

## The server (step 5.1)

```powershell
.\artpipe.cmd agent setup     # once: the server's venv (Python 3.12 + the locked MCP SDK)
.\artpipe.cmd agent status    # JSON: venv, Python, installed mcp, lock hash
```

`.mcp.json` at the repo root registers it for Claude Code
(`cmd /d /c artpipe.cmd agent serve`, stdio). Open the repo in Claude Code
and run `/mcp` to see it.

**A thin front end.** The server (`agent/server.py`) runs each tool as an
artpipe command (`agent/core/commands.py`) and returns its exit code, its
meaning and what it wrote. It holds no pipeline logic: the agent sees what
a human at the CLI sees, and the rules are applied only by the tested
Python 3.9 code.

**Its own Python.** The official MCP SDK needs Python 3.10+, mayapy is 3.9.
`artpipe agent setup` builds `%LOCALAPPDATA%\artpipe\agent-venv` (or
`ARTPIPE_TOOLS`) from the py launcher's 3.12 and `agent/requirements.lock`:
30 wheels, exact versions and sha256, installed with `--require-hashes
--no-deps` (no resolver, no newer transitive dependency). The lock is made
by `agent/lock.py` from downloaded wheels; it is for CPython 3.12 on
win_amd64. PyYAML is the same 6.0.2 mayapy uses, so both read the rules
alike. The SDK is pinned to 2.2.0 (released 2026-09-07; 2.3.0 was 18 days
old). A test checks that `agent.yaml`, the lock, `artpipe.cmd` and
`.mcp.json` agree.

Probed while building it:

| Probe | Result |
|---|---|
| `mcp` 2.x API | `FastMCP` is gone (renamed `MCPServer`, `mcp.server.mcpserver`), and models are snake_case (`input_schema`, `is_error`, `structured_content`): v1 examples don't run |
| Spawning the server with the SDK's stdio client | **the server gets a minimal environment**: the SDK passes 12 variables on Windows (APPDATA, HOMEDRIVE, HOMEPATH, LOCALAPPDATA, PATH, PATHEXT, PROCESSOR_ARCHITECTURE, SYSTEMDRIVE, SYSTEMROOT, TEMP, USERNAME, USERPROFILE). No `ProgramFiles`, so `artpipe.cmd` couldn't find mayapy ("mayapy not found") and no `COMSPEC`. `artpipe.cmd` now falls back to the defaults. Your own `MAYAPY`, `UNITY` or `ARTPIPE_TOOLS` must go in the `env` of `.mcp.json`: they won't be inherited |
| `subprocess.run(timeout=3)` on an artpipe command that sleeps 25 s | **returned after 25 s**, and mayapy kept running: it kills `cmd.exe`, then waits for the pipes, which the mayapy child still holds. The runner now kills the whole tree (`taskkill /T /F`): 3.2 s, no orphan |
| A real call over stdio (`artpipe_info`) | connect ~1.1 s, call ~0.35 s (mayapy without Maya loaded is fast) |

**Arguments are data, never syntax.** Every tool argument reaches
`cmd.exe`, so `& | < > ^ % "` and newlines are refused before anything runs
(`commands.check_args`): a prompt like `a coin & del *` can't become a
second command.

The 5.1 tool: `artpipe_info` (repo commit, every conventions file with its
sha256, the agent pins, the banner renderer's tool status). Call it first.

Tests: `tests/test_agent_core.py` (rules, the argument filter, a real
command, the timeout killing the tree) and `tests/test_agent_server.py`
(marker `agent`: the real server over stdio from the SDK's client, minimal
environment included; removing the `ProgramFiles` fallback turns it red).

## Jobs, the GPU queue, ComfyUI sharing (step 5.2)

A tool that would outlast a call (a render, a generation, a batch) starts a
**job** and returns its id at once. The agent then calls `job_status`
(state, queue position, log tail, and for GPU jobs ComfyUI's step
progress), `job_result` (exit code, meaning, full output) or `job_cancel`;
`job_list` shows them all. Tools so far: `banners_render`, `gen_concept`
(step 5.5 adds the rest).

```
out/agent/jobs/<job_id>/job.json     state machine (conventions/agent_job.schema.json)
                        spawn.json   how the runner was started and its pid (written by the starter)
                        runner.pid   the runner's own pid (written by the runner)
                        stdout.log / stderr.log   the command's live output
```

States: `queued` → `running` → `done` (the command exited; its exit code
keeps the 0 / 1 / 2 meaning) | `cancelled` | `lost` (the runner died, or a
queued job never got one within 2 minutes). Each file has one writer, so the
starter and the runner never overwrite each other.

**The runner** (`artpipe agent job-run ID`, `agent/runner.py`) is a detached
mayapy process: GPU jobs wait for the `gpu` lock (one at a time, first come
first served), the command's output streams into the logs, the job's
timeout kills the whole process tree, and the lock is released however the
job ends. A runner killed outright leaves a stale lock (dead pid) that the
next job takes over.

**Locks** (`validate/core/locks.py`) are files holding the holder's pid,
created with O_EXCL (of two racers exactly one wins); a dead holder's lock,
or an empty one older than 10 s, is stale.

**Sharing ComfyUI (backlog #45).** `gen.comfy.session()` replaces the
start/stop pairs: it starts the server under a start lock, registers its
user (`out/gen/comfy/users/`), and on exit stops the server only if no live
user remains, it isn't kept (`gen server start`, `--keep-server`), and it is
the pipeline's (started by this session, not running when it began, or
recorded in `server.json` with a live pid). Repair loops and `artpipe
generate` hold an outer session that registers without starting, so the
server stays up between attempts and stops once at the end.

**Progress (backlog #44).** Probed: ComfyUI's sampler and decoder bars are
tqdm lines in its log, redrawn with `\r` (` 75%|#######5 | 6/8 [00:05<00:01,
1.13it/s]`). `gen.comfy.progress()` reads the latest one, from the byte
offset where the job started (so the previous job's 100 % isn't reported).
No websocket client needed. Seen over MCP on a real concept job: 0/8 "Model
Initializing", 1/8 ... 8/8, then done.

Probed while building it:

| Probe | Result |
|---|---|
| A detached runner started by the server, then the session closed | **the job came back `lost`**: the SDK's stdio client puts the server in a Windows Job Object with KILL_ON_JOB_CLOSE, and closing the session kills every member, detached or not |
| The same with `CREATE_BREAKAWAY_FROM_JOB` | CreateProcess accepted the flag, the runner **still died** (nested jobs) |
| The runner created by WMI (`Win32_Process.Create`) when the server is in a job (`IsProcessInJob`) | survives: started by one server process, followed to `done` by another. ~1 s to spawn; the WMI-created process gets the user's full environment |
| `start()` writing job.json after spawning | with WMI's delay, a job already `running` was put back to `queued`: hence one writer per file |
| Old `queued` records from broken runs | counted in the queue position forever; now `lost` after the grace and ignored by the queue |

Tests: `tests/test_locks.py`, `tests/test_comfy_session.py` (nesting,
another live user, a dead user's registration, kept servers, a server
someone else runs, a raising block, the start-lock timeout, progress
offsets), `tests/test_agent_jobs.py` (real detached runners: done, exit
codes, timeout, cancel while running and while queued, FIFO without overlap,
lost, stale lock, refused syntax, the schema), and over MCP
(`tests/test_agent_server.py`: a render job that survives a server restart;
under `comfy`, a GPU job's progress).

## The persistent Maya worker (step 5.3, backlog #1)

`validate/maya/worker.py` boots Maya once and validates file after file;
`agent/core/maya_worker.py` is its client (start on first use, ping, call,
restart once if it died). `artpipe agent maya-worker start|stop|status` for
a human.

* **Channel**: localhost TCP, one JSON line per request and reply. Not
  stdout: Maya prints its own noise there.
* **Token**: the worker writes `out/agent/maya_worker.json` (pid, port, a
  random token); a request without the token is refused, so another local
  process can't drive Maya.
* **Same code path**: `validate.maya.cli` is split into `main()` (starts
  and stops Maya) and `run()` (the command in a running Maya); the worker
  runs `run()` for every request, and every file opens into a fresh scene.
  argparse errors and crashes become exit 2 replies; the worker lives on.
* **Lifetime**: one request at a time (Maya isn't thread-safe), exits after
  `--idle-s` (15 min default). Spawned like a job runner (WMI inside a Job
  Object), so it outlives the MCP session that started it.

Measured on SM_Messy.ma:

| | Time |
|---|---|
| cold `artpipe validate` | 4.6 s today (21 s when backlog #1 was written, on a cold disk cache) |
| worker boot (once) | 4.9 s |
| warm validate | 0.04-0.12 s round trip |
| warm validate + fix + export (FBX re-validated) | 0.54 s |

Proven (`tests/test_maya_worker.py`, marker `maya`): the worker writes the
same report bytes as the cold CLI; fix + export give the same `.ma` and FBX
bytes (reports identical once their own output folder is normalised); a
clean / messy / clean sequence doesn't leak state (first and last reports
identical); a bad token is refused; an argparse error doesn't kill it; a
killed worker is restarted on the next call.

## Fine tools: one check, one fix (step 5.4)

| Tool | Runs | Returns |
|---|---|---|
| `list_checks(domain)` | the registry: `maya` (20 checks, 9 fixes, from the worker), `kit`, `banner` (`artpipe banners checks`) | id, severity, fixable, description per check |
| `run_check(file, asset_class, check_id)` | `validate --checks ID` in the warm Maya worker | that one result: passed, severity, message, metrics, offenders |
| `apply_fix(file, asset_class, fix_id)` | `validate --fix --fixes ID` in the worker, the fixed copy under `out/agent/calls/` | what the fix did (or why it was skipped), the fixed file, the re-validation's summary and failing checks |
| `banners_check(folder, check_ids)` | `banners check FOLDER --checks ...` | status and each result |

The CLI gained the filters the tools need: `validate --checks ID,ID` (a
PARTIAL report: an agent asking one question), `--fix --fixes ID,ID` (apply
only those, then re-validate with every check), `banners check --checks`.
Unknown ids are a tool error (exit 2) before any work; `--fixes` without
`--fix` and `--checks` with `--export` are refused (a partial report must
never gate an FBX into Unity). The source scene is never touched; every
call writes into its own folder under `out/agent/calls/`.

Probed while building it: **a batch file splits an unquoted argument on
`,` `;` `=`** (and spaces): `--checks scene.history,naming.convention`
reached the validator as two arguments, so it ran nothing it should. Python's
`list2cmdline` quotes only arguments with spaces, so the runner now builds
the command line itself: every argument in double quotes (no quote can be
inside one: `check_args` refuses them), the whole wrapped for `cmd /s /c`,
which strips exactly the outer pair. A test pushes `a,b`, `x;y`, `k=v`,
`a, red, toadstool`, a tab and `!bang` through and gets them back intact.
Phase 3 met the same splitting on `=` and worked around it (`--param NAME
VALUE`); this fixes it for every tool.

## Coarse tools (step 5.5)

| Tool | Kind | Writes |
|---|---|---|
| `validate(file, asset_class, fix, export)` | warm Maya worker | `out/agent/calls/validate-*/` (report, fixed `.ma`, FBX + its report) |
| `texture_check(files, asset_class)` | call | nothing |
| `gen_concept`, `gen_model` | GPU jobs | `out/gen/runs/`, `out/gen/repairs/` |
| `gen_ingest(model_run, asset_class, name)` | job (Maya) | `out/gen/assets/<name>/` |
| `banners_kit(kit, template)` | call | `out/banners/kits/` |
| `banners_render`, `banners_batch` | jobs | `out/banners/` |
| `banners_check` | call | the banner folder's `banner.report.json` |
| `unity_verify` | job (Unity, read-only) | nothing in the project |
| `read_report(path)` | call | nothing: any JSON or log under `out/` |
| `read_conventions(name)` | call | nothing: any file in `conventions/` ("never guess a number") |

Everything here writes only under `out/`. The tools that write into the
Unity project (`build`, `generate`) or overwrite kit art (`banners art
...`) are not exposed: they go through the approval queue (step 5.6), and a
test asserts they're absent from the tool list. `read_report` and
`read_conventions` refuse any path that resolves outside their folder
(`../`, absolute paths, `out/../CLAUDE.md`).

Probed: **the SDK hides every exception message except its own
`ToolError`**: a refused path reached the agent as "Error executing tool
read_report", with no reason. Every tool is now registered through
`@tool`, which turns anticipated failures (ValueError, KeyError, OSError,
the pipeline's ToolError) into the SDK's `ToolError` with the message
("ValueError: CLAUDE.md is outside out: refused"; "KeyError: no job ...").
A real bug stays hidden from the model and goes to the server log. A test
checks that the wrapper keeps every tool's input schema (`functools.wraps`).

Tested over MCP (`tests/test_agent_coarse_tools.py`): validate plain and
with fix + export (exit 1 then 0, every file under `out/agent/calls/`),
texture_check, banners_kit (its report path and results), the four path
refusals with their reason, read_conventions and read_report, a
banners_batch job (handed over: the 1 s spot), unity_verify (marker unity),
gen_ingest on an existing model run (marker maya), the input schemas, and
the gated tools' absence. `gen_model` uses the same GPU job path as
`gen_concept` (proven with live progress in 5.2) and the Phase 3 CLI; a
full image-to-3D over MCP (~5 GPU minutes) isn't in the suite.

## The approval queue (step 5.6)

```
out/agent/queue/<id>.json      conventions/agent_queue.schema.json
```

**Approvals.** A gated action (`conventions/agent.yaml` `gated`: `build`,
`generate`, `banners_art_hero`, `banners_art_background`,
`banners_art_logo`, `golden_rerecord`: everything that writes outside `out/`)
is never a tool. The agent calls `queue_request(action, params, reason)`:
the item stores the exact artpipe command, its sha256, what it will write
("imports the asset into unity/Assets") and the agent's reason. A human
decides on the command line:

```powershell
.\artpipe.cmd queue list
.\artpipe.cmd queue show approval-1a2b3c4d5e
.\artpipe.cmd queue approve approval-1a2b3c4d5e --note "ok, first coin of the batch"
```

Then the agent calls `queue_run(id)`, which starts the job only if the item
is approved, hasn't run yet, and its command is unchanged (the stored
sha256, and the parameters rebuilt into the same command). An approval runs
once. There is no MCP tool to approve, reject or resolve: a test asserts it.

**Hand-overs.** Every job that ends with exit 1 (an asset or banner the
repair loop handed over, a failed check nothing could fix) files a
hand-over item (one per job) with the lines a human should read first: the
verdict and each failing check. `artpipe queue resolve ID --note ...`
closes it.

**What it does not protect against.** Approvals are files. The MCP server
gives the agent no way to approve, but an agent that also has file-editing
tools (Claude Code does) could write an "approved" item itself; the sha256
protects the command, not the decision. Two layers close that: Claude Code
permission rules that deny writes to `out/agent/queue/` (and the agent's
own repo config), and, for teams, signed decisions (backlog #65, with #38).
`.claude/settings.json` (committed) now denies Claude Code's file tools any
write under `out/agent/queue/` and `.claude/` (so the rule can't be edited
away); probed: a Write into the queue folder is refused. A shell command
could still write there, so #65 stays open for signed decisions.

Tested (`tests/test_agent_queue.py`): the gated list in conventions equals
the queue's actions; a request does nothing; bad requests are refused (an
unknown action, a missing parameter, no reason, cmd.exe syntax in a prompt);
approve-then-run once; a rejected item never runs; a command or parameters
edited after approval are refused; hand-overs are one per job and resolve;
ids are checked; and over MCP the full round trip (request, `queue_run`
refused while pending, `artpipe queue approve` by the human, `queue_run`
regenerates the logo of a kit copy, a second run refused), no
approve-like tool exists, and a job that exits 1 lands in the queue.

## Judgement tools (step 5.7, backlog #48, #53)

The pipeline doesn't judge taste; the client's model does, by looking. The
review tools return images (MCP image content) with the facts and the
questions no check answers:

| Tool | Returns | Questions it asks the model |
|---|---|---|
| `review_concept(concept_run)` | the concept image, its mask, the concept checks, the prompt | the right object? a shaded 3D object, not a flat 2D drawing? any text or logo? the game's style? |
| `review_banner(folder)` | the contact sheet (decoded key frames), the report | does the hero stand out at thumbnail size? natural line breaks? does it look like this game? would a UA lead ship it? |
| `review_image(path)` | any PNG under `out/` or `banners/kits/` | |
| `record_review(target, verdict, notes)` | the stored review (`out/agent/reviews/`) | |
| `retry_concept(previous_run, subject, asset_class, style, reason)` | a GPU job with the new subject | |

The questions come from what Phase 3 and 4 measured the checks missing
(`docs/generation.md` "What the checks can't see yet": the potion drawn as
a flat icon; this phase's red-mushroom background).

A verdict is a record, not a gate: the pipeline stays deterministic, and a
human reads what the agent decided and why. A new prompt is bounded:
`judgement.max_prompt_changes` (2) per original concept, every change with
its reason, in `out/agent/reviews/lineage/`; a retry of a retry counts
against the same original (its run is resolved from the job's output). Past
the budget, `retry_concept` refuses before any GPU time: a human decides.
Images over `judgement.max_image_bytes` (5 MB) are refused rather than
sent.

Tested (`tests/test_agent_judgement.py`): verdict and notes required, the
budget (exactly the max allowed, then refused), a retry's run counted
against its original, and over MCP: `review_concept` returns text then a
PNG with the questions, `record_review` stores the verdict, path refusals,
a kit image, `retry_concept` refused past the budget, and `review_banner`
returning the sheet of a real render.

## Example sessions (step 5.8)

Three sessions as an agent runs them, tool calls abbreviated (`→` is what
comes back). The full tool reference is [agent_tools.md](agent_tools.md),
generated from the server (`artpipe agent docs`; a test fails if it is
stale).

**1. Validate, fix, re-check (free: everything lands in `out/`).**

```
artpipe_info()                                → versions, conventions sha256s, gated actions
read_conventions("mesh_budgets")              → the class budgets (never guess a number)
validate(file="out/fixtures/SM_Messy.ma", asset_class="prop_small")
                                              → exit 1, the report path, the failing check ids
list_checks()                                 → which of those have a safe fix
apply_fix(file=..., asset_class="prop_small", fix_id="transform.frozen")
                                              → exit 0, the fixed .ma under out/agent/calls/, the fix record
run_check(file=<the fixed .ma>, asset_class="prop_small", check_id="transform.frozen")
                                              → passed: the agent moves on to the next failure
validate(file=<the fixed .ma>, asset_class="prop_small", export=true)
                                              → exit 0, canonical FBX + linked report in out/
```

Each fine call runs in the Maya worker (0.04-0.12 s), not a 4.6 s cold
mayapy. Unfixable failures (an over-budget mesh) stay exit 1: the agent
reports them, it doesn't edit geometry.

**2. A banner batch, a review, a hand-over.**

```
banners_kit(kit="spotcap_grove", template="hero_endcard")   → exit 0, kit checks
banners_batch(template="hero_endcard", games="spotcap_grove")
                                              → a job id (CPU job: render, checks, repair)
job_status(job_id)                            → running, banner 1 of N
job_result(job_id)                            → exit 1: one banner handed over
queue_list(kind="handover")                   → the item: "banner.text_fit: headline still doesn't fit..."
review_banner(folder=<a passing banner>)      → the contact sheet (image) + the questions
record_review(target=<folder>, verdict="ship", notes="hero reads at thumbnail size")
```

The hand-over waits for a human (`artpipe queue resolve ID --note ...`)
once the copy is shortened; the next batch reuses every banner whose inputs
didn't change.

**3. A gated build.**

```
queue_request(action="build",
              params='{"scene": "out/agent/calls/<call>/SM_Messy.ma", "asset_class": "prop_small"}',
              reason="first prop of the batch; all Maya checks pass")
                                              → approval-…, pending, the exact command
queue_run(item_id)                            → refused: pending
      (the human: artpipe queue show approval-… ; artpipe queue approve approval-… --note "ok")
queue_run(item_id)                            → a job id; the build imports into unity/Assets
job_result(job_id)                            → exit 0, build report, prefab path
unity_verify()                                → read-only: every asset under Assets/Art re-checked
queue_run(item_id)                            → refused: an approval runs once
```

## Tests, mutation pass and docs (step 5.8)

- `agent/core/safety.py`: the path guard every tool uses (`inside`), moved
  out of the server so it is unit-tested without an MCP client
  (`tests/test_agent_safety.py`: `out_evil/` is not under `out/`, `..`
  escapes, absolute paths, several roots).
- `tests/mutation_agent.py` (`artpipe py tests/mutation_agent.py`): 17
  planted bugs over the queue, the path guard, the argument filter and
  quoting, jobs, locks, the ComfyUI session and the prompt budget, each run
  against the agent tests that need no MCP client. First pass: 16 killed;
  "FIFO ignored" survived because only a live-runner test proved the GPU
  FIFO, so `first_in_line` got a pure test. Second pass: 17 of 17 "killed",
  but not honestly: runners spawned by the live job tests while a mutant
  was planted had loaded the mutated `locks.py` and kept it after the file
  was restored. One never took over a dead GPU lock, FIFO held 20 later
  jobs behind it, and the live job tests failed under every later mutant,
  whatever it was. The pass now cancels every job a mutant's tests started.
  Third pass, clean: two real survivors. "Edited command accepted" (the
  tamper test edited args or params, and the parameter rebuild catches
  either on its own; only a consistent edit of both needs the sha256: now a
  test case) and "stops a kept server" (both kept cases would not have
  stopped anyway; the missing case is a server the session started and
  someone marks kept meanwhile: now a test). Fourth pass: 17 of 17. A
  waiting runner runs the code it was spawned with outside tests too
  (backlog #68).
- `docs/agent_tools.md`: generated from the server's own `list_tools`
  (names, descriptions, input schemas, grouped); `artpipe agent docs
  --check` fails on drift, and a test runs it.
- [architecture.md](architecture.md): the one-page overview for a reviewer.

## What the agent layer can't do yet

- **Approve safely across a team.** Approvals are files; the Claude Code
  permission rules stop the agent's file tools, not a shell (#65). Signed
  decisions and signed reports (#38) are the fix.
- **Drive Unity cheaply.** Every Unity call boots batchmode (15-20 s warm);
  there is no Unity worker like the Maya one (#30).
- **Escalate past a seed.** The repair loops re-roll seeds and the agent can
  reword a concept twice; a structural failure (a crate's slats folding in
  their UV charts) still goes to a human (#53).
- **See a 3D model.** The judgement tools return 2D images (concept, mask,
  banner frames). A turntable render of the generated mesh would let the
  model judge shape and texture fidelity (#66, #56).
- **Run anywhere but this machine.** Windows, a local GPU, Maya 2023 and a
  licensed Unity; no CI (#16, #31).
