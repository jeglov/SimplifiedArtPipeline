"""artpipe as an MCP server (stdio). Started by `artpipe agent serve` in the agent venv (Python 3.12).

The server is a thin front end: each tool runs an artpipe command
(agent/core/commands.py) and returns its exit code, its verdict and the
JSON reports it wrote. It holds no pipeline logic, so the agent sees exactly
what a human at the CLI sees, and the tested Python 3.9 code stays the only
place where rules are applied. Judgement (is this background right for the
game? which prompt next?) stays with the client: the server never calls an
LLM.

Exit codes in results keep the pipeline's contract: 0 pass / 1 the asset
failed (or was handed over) / 2 tool error.
"""
from __future__ import annotations

import functools
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError

from agent.core import ToolError as AgentToolError
from agent.core import commands
from agent.core import jobs
from agent.core import rules as agent_rules

ROOT = commands.ROOT
RULES = agent_rules.load()

INSTRUCTIONS = """\
artpipe: an art pipeline for mobile games (Maya -> Unity assets, AI generation, UA video creatives).
Every tool runs one artpipe command and returns {exit_code, meaning, ...}: 0 pass, 1 the asset failed its
checks or was handed over (read the report: it says what to fix), 2 the tool broke (retry or tell a human).
Long tools (renders, generations, batches) return a job id at once: poll job_status (it shows progress and
the queue position; GPU jobs run one at a time), then read job_result. Jobs survive a server restart.
Rules live in conventions/ (read them with the info tool); never guess a budget or a spec.
"""

server = MCPServer(RULES.server.name, instructions=INSTRUCTIONS, version="phase5")

# Failures an agent can act on (a refused path, an unknown job or check id, a
# missing file, a tool error the pipeline reports). Probed: the SDK shows the
# model ONLY "Error executing tool <name>" for any exception other than its own
# ToolError, so a refusal without its reason is useless. These are re-raised
# as the SDK's ToolError with our message; anything else is a bug and stays
# hidden (the server logs its traceback).
ANTICIPATED = (ValueError, KeyError, OSError, AgentToolError)


def tool(**kw):
    def deco(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except ANTICIPATED as exc:
                msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
                raise SdkToolError("%s: %s" % (type(exc).__name__, msg)) from exc
        server.tool(**kw)(wrapped)
        return fn
    return deco


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, universal_newlines=True).stdout.strip()
    except OSError:
        return ""


def _json_or_text(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text.strip()


@tool(description="What this artpipe is: repo commit, the rules files (with sha256), the agent "
                         "layer's pins, and whether the banner renderer's pinned tools are installed. "
                         "Call it first in a session.")
def artpipe_info() -> Dict[str, Any]:
    conventions = {p.name: _sha(p) for p in sorted((ROOT / "conventions").glob("*")) if p.is_file()}
    banners = commands.run(["banners", "status"], RULES.server.call_timeout_s)
    return {
        "repo": {"head": _git_head()},
        "agent": {"python": sys.version.split()[0], "mcp_pinned": RULES.mcp_version,
                  "lock_sha256": _sha(ROOT / RULES.lock)},
        "conventions": conventions,
        "banner_tools": {"exit_code": banners.exit_code, "status": _json_or_text(banners.stdout)},
    }


# --- jobs (step 5.2) ------------------------------------------------------------------

JOB_TIMEOUT_S = {"cpu": 3 * 3600, "gpu": 3 * 3600}


def _start(tool: str, args, resource: str = "cpu", meta=None) -> Dict[str, Any]:
    rec = jobs.start(tool, args, resource, JOB_TIMEOUT_S[resource], meta)
    return {"job_id": rec["id"], "state": rec["state"], "resource": resource,
            "command": "artpipe " + " ".join(rec["args"]),
            "next": "poll job_status(job_id) until the state is done, then job_result(job_id)"}


def _gpu_progress(job_id: str):
    rec = jobs.read(job_id)
    if rec["resource"] != "gpu" or rec["state"] != "running":
        return None
    from gen import comfy                      # stdlib-only module: importable here
    return comfy.progress(since=int(rec.get("comfy_log_offset") or 0))


@tool(description="State of a job: queued (with its queue position), running (with the log tail and, "
                         "for GPU jobs, ComfyUI's step progress), done (exit code and meaning), cancelled or lost.")
def job_status(job_id: str) -> Dict[str, Any]:
    out = jobs.status(job_id)
    prog = _gpu_progress(job_id)
    if prog:
        out["comfy_progress"] = prog
    return out


@tool(description="A finished job's exit code, meaning and full output (capped). Before it finishes, "
                         "the same as job_status.")
def job_result(job_id: str) -> Dict[str, Any]:
    return jobs.result(job_id)


@tool(description="Cancel a queued or running job; its command and every process under it are killed.")
def job_cancel(job_id: str) -> Dict[str, Any]:
    return jobs.cancel(job_id)


@tool(description="Every job with its state, newest last. state filters (queued, running, done, "
                         "cancelled, lost).")
def job_list(state: str = "") -> Dict[str, Any]:
    rows = [{k: r[k] for k in ("id", "tool", "state", "resource", "created", "exit_code", "meaning")}
            for r in jobs.list_jobs() if not state or r["state"] == state]
    return {"jobs": rows, "count": len(rows)}


@tool(description="Render one UA banner (template x kit x placement x locale), check it, and with "
                         "repair=True fix and re-render it per conventions/banners.yaml. A job (~2-5 min). "
                         "Result: exit 0 pass / 1 handed over (repair.json says why) / 2 tool error; the banner "
                         "folder is out/banners/<kit>/<template>/<placement>/<locale>/.")
def banners_render(kit: str, template: str, placement: str = "ua_video_9x16", locale: str = "en",
                   repair: bool = True) -> Dict[str, Any]:
    args = ["banners", "render", kit, "--template", template, "--placement", placement, "--locale", locale]
    return _start("banners_render", args + (["--repair"] if repair else []))


@tool(description="Generate a concept image for an asset on the pinned ComfyUI and check it for "
                         "image-to-3D (artpipe gen concept). A GPU job: queued behind other GPU jobs. "
                         "style is an id from conventions/gen_styles.yaml, asset_class from mesh_budgets.yaml.")
def gen_concept(subject: str, asset_class: str, style: str, seed: int = 0, repair: bool = True) -> Dict[str, Any]:
    args = ["gen", "concept", subject, "--class", asset_class, "--style", style, "--seed", str(seed)]
    return _start("gen_concept", args + (["--repair"] if repair else []), resource="gpu")


# --- fine tools: one check, one fix (step 5.4) -------------------------------------------

CALLS = ROOT / "out" / "agent" / "calls"


def _call_dir(kind: str) -> Path:
    import uuid
    d = CALLS / ("%s-%s" % (kind, uuid.uuid4().hex[:10]))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


def _maya(args) -> Dict[str, Any]:
    from agent.core import maya_worker
    reply = maya_worker.call("validate", args)
    return {"exit_code": reply["exit_code"], "meaning": commands.EXIT_MEANING.get(reply["exit_code"]),
            "seconds": reply["seconds"]}


def _report(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@tool(description="List the checks (and fixes) you can run. domain: maya (scene checks + safe fixes), "
                         "kit (game kit checks), banner (rendered banner checks). Each entry: id, severity "
                         "(error blocks, warning informs), fixable, description.")
def list_checks(domain: str = "maya") -> Dict[str, Any]:
    if domain == "maya":
        from agent.core import maya_worker
        reply = maya_worker.call("list_checks", [])
        return json.loads(reply["stdout"].strip().splitlines()[-1])
    if domain in ("kit", "banner"):
        out = commands.run(["banners", "checks"], RULES.server.call_timeout_s)
        return {"checks": json.loads(out.stdout)[domain]}
    raise ValueError("domain must be maya, kit or banner")


@tool(description="Run ONE Maya check on a scene (.ma/.mb/.fbx, repo-relative or absolute) for an asset "
                         "class (conventions/mesh_budgets.yaml). Returns that check's result: passed, severity, "
                         "message, metrics, offenders (component names). Warm Maya worker: well under a second.")
def run_check(file: str, asset_class: str, check_id: str) -> Dict[str, Any]:
    d = _call_dir("check")
    out = _maya([file, "--class", asset_class, "--checks", check_id, "--out", str(d)])
    rep = _report(d / (Path(file).name + ".report.json"))
    out.update(result=(rep.get("results") or [None])[0], tool_error=rep.get("tool_error"),
               report=_rel(d / (Path(file).name + ".report.json")))
    return out


@tool(description="Apply ONE safe fix to a scene and re-validate the fixed copy with every check. The "
                         "source is never touched: the fixed scene is written as .ma under out/agent/calls/. "
                         "Returns what the fix did (or why it was skipped), the fixed file and the new report's "
                         "summary and failing checks.")
def apply_fix(file: str, asset_class: str, fix_id: str) -> Dict[str, Any]:
    d = _call_dir("fix")
    out = _maya([file, "--class", asset_class, "--fix", "--fixes", fix_id, "--fixed-dir", str(d / "fixed"),
                 "--out", str(d)])
    rep = _report(d / (Path(file).name + ".report.json"))
    fixed = d / "fixed" / (Path(file).stem + ".ma")
    out.update(fixes=rep.get("fixes"), fixed_file=_rel(fixed) if fixed.is_file() else None,
               status=rep.get("status"), summary=rep.get("summary"), tool_error=rep.get("tool_error"),
               failing=[r["id"] for r in rep.get("results") or [] if not r["passed"]],
               report=_rel(d / (Path(file).name + ".report.json")))
    return out


@tool(description="Re-run banner checks on a rendered banner folder (out/banners/<kit>/<template>/"
                         "<placement>/<locale>), all of them or only check_ids (comma-separated). Returns the "
                         "report's status and each result. Writes banner.report.json in that folder.")
def banners_check(folder: str, check_ids: str = "") -> Dict[str, Any]:
    args = ["banners", "check", folder] + (["--checks", check_ids] if check_ids else [])
    out = commands.run(args, RULES.server.call_timeout_s)
    rep = _report((ROOT / folder if not Path(folder).is_absolute() else Path(folder)) / "banner.report.json")
    return dict(out.to_dict(), status=rep.get("status"), results=rep.get("results"))


# --- coarse tools (step 5.5) -------------------------------------------------------------
#
# Everything here writes only under out/ (or nowhere). Tools that write into
# the Unity project or overwrite kit art (build, generate, banners art) go
# through the approval queue (step 5.6).

def _inside(path: str, *roots: str) -> Path:
    """A path the agent named, resolved and refused unless it lies under one of `roots` (repo-relative)."""
    p = (ROOT / path) if not Path(path).is_absolute() else Path(path)
    p = p.resolve()
    for r in roots:
        base = (ROOT / r).resolve()
        if p == base or base in p.parents:
            return p
    raise ValueError("%s is outside %s: refused" % (path, ", ".join(roots)))


@tool(description="Validate a Maya scene (.ma/.mb/.fbx) against its asset class: every check, optional "
                         "safe fixes (fix=True: a fixed .ma copy, the source is never touched) and optional FBX "
                         "export of a passing asset (export=True). Warm Maya worker. Returns exit code, the "
                         "report's status and summary, the failing checks with their messages, and the files "
                         "written (all under out/agent/calls/).")
def validate(file: str, asset_class: str, fix: bool = False, export: bool = False) -> Dict[str, Any]:
    d = _call_dir("validate")
    args = [file, "--class", asset_class, "--out", str(d)]
    if fix:
        args += ["--fix", "--fixed-dir", str(d / "fixed")]
    if export:
        args += ["--export", str(d / "fbx")]
    out = _maya(args)
    rep = _report(d / (Path(file).name + ".report.json"))
    out.update(status=rep.get("status"), summary=rep.get("summary"), tool_error=rep.get("tool_error"),
               failing=[{"id": r["id"], "severity": r["severity"], "message": r["message"]}
                        for r in rep.get("results") or [] if not r["passed"]],
               fixes=[f for f in rep.get("fixes") or [] if f.get("applied")],
               files=sorted(_rel(p) for p in d.rglob("*") if p.is_file()))
    return out


@tool(description="Check textures (PNG/TGA) against conventions/texture_rules.yaml in pure Python "
                         "(name, size, power of two, alpha...): the same check ids Unity applies on import.")
def texture_check(files: str, asset_class: str = "") -> Dict[str, Any]:
    paths = [f.strip() for f in files.split(",") if f.strip()]
    args = ["texture"] + paths + (["--class", asset_class] if asset_class else [])
    return commands.run(args, RULES.server.call_timeout_s).to_dict()


@tool(description="Image to a low-poly textured GLB (TRELLIS.2) from a passing concept run "
                         "(out/gen/runs/concept_zimage-...), with the model checks; repair=True retries by seed "
                         "per generation.yaml. A GPU job (several minutes on 8 GB).")
def gen_model(concept_run: str, asset_class: str, seed: int = 56, repair: bool = True) -> Dict[str, Any]:
    args = ["gen", "model", concept_run, "--class", asset_class, "--seed", str(seed)]
    return _start("gen_model", args + (["--repair"] if repair else []), resource="gpu")


@tool(description="A passing model run (out/gen/runs/model_trellis2-...) to a Maya scene + textures under "
                         "out/gen/assets/<name>/ (SM_<name>.ma, T_<name>_D/N.png). A job (Maya). Then validate it.")
def gen_ingest(model_run: str, asset_class: str, name: str) -> Dict[str, Any]:
    return _start("gen_ingest", ["gen", "ingest", model_run, "--class", asset_class, "--name", name])


@tool(description="Run the kit checks (slots, files, licences, colours, images, background) for a game "
                         "kit against a template. Returns exit code and the kit report's results.")
def banners_kit(kit: str, template: str, locale: str = "en") -> Dict[str, Any]:
    out = commands.run(["banners", "kit", kit, "--template", template, "--locale", locale],
                       RULES.server.call_timeout_s)
    # out/banners/kits/<kit id>.<template id>.kit.report.json (banners/cli.py check_kit)
    path = ROOT / "out" / "banners" / "kits" / ("%s.%s.kit.report.json" % (Path(kit).name, Path(template).stem))
    rep = _report(path)
    return dict(out.to_dict(), status=rep.get("status"), results=rep.get("results"),
                report=_rel(path) if path.is_file() else None)


@tool(description="Every game x placement x locale of a template, rendered through the repair loop, reused "
                         "when nothing changed (input hash). games: comma-separated kit ids. A job; the summary "
                         "is out/banners/batch/<template>.batch.json.")
def banners_batch(template: str, games: str, repair: bool = True) -> Dict[str, Any]:
    args = ["banners", "batch", "--template", template, "--games", games]
    return _start("banners_batch", args + ([] if repair else ["--no-repair"]))


@tool(description="Re-check every asset already in the Unity project (Assets/Art) from the files, with no "
                         "import cache: read-only. A job (Unity boots in batchmode, ~20 s warm).")
def unity_verify() -> Dict[str, Any]:
    return _start("unity_verify", ["unity", "verify"])


@tool(description="Read a JSON report or record the pipeline wrote (anything under out/: reports, repair "
                         "records, batch summaries, provenance, job logs). Path repo-relative.")
def read_report(path: str) -> Dict[str, Any]:
    p = _inside(path, "out")
    if p.stat().st_size > 2_000_000:
        raise ValueError("%s is over 2 MB: read a narrower file" % path)
    text = p.read_text(encoding="utf-8", errors="replace")
    try:
        return {"path": _rel(p), "json": json.loads(text)}
    except ValueError:
        return {"path": _rel(p), "text": text[-20000:]}


@tool(description="Read a rules file from conventions/ (budgets, naming, textures, banner specs, repair "
                         "policies, schemas). Never guess a number: read it here. name: e.g. mesh_budgets.yaml.")
def read_conventions(name: str) -> Dict[str, Any]:
    p = _inside("conventions/" + name, "conventions")
    return {"name": p.name, "text": p.read_text(encoding="utf-8")}


# --- the approval queue (step 5.6) ---------------------------------------------------------

from agent.core import queue  # noqa: E402  (grouped with its tools)


@tool(description="Ask a human to approve a GATED action (it writes outside out/): build (scene, asset_class, "
                  "textures) and generate (subject, asset_class, style, name) import into unity/Assets; "
                  "banners_art_hero (kit, concept_run), banners_art_background (kit, prompt, seed, repair) and "
                  "banners_art_logo (kit) overwrite kit art; golden_rerecord changes the golden banner. params: a "
                  "JSON object. reason: why, in a sentence a human reads before approving. Nothing runs until a "
                  "human runs `artpipe queue approve ID`; then call queue_run(ID).")
def queue_request(action: str, params: str, reason: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(params) if params.strip() else {}
    except ValueError:
        raise ValueError("params must be a JSON object, e.g. {\"kit\": \"spotcap_grove\"}")
    if not isinstance(parsed, dict):
        raise ValueError("params must be a JSON object")
    item = queue.request(action, parsed, reason)
    return {"id": item["id"], "status": item["status"], "effect": item["effect"],
            "command": "artpipe " + " ".join(item["args"]),
            "next": "a human approves with `artpipe queue approve %s`; then call queue_run" % item["id"]}


@tool(description="The approval queue: approvals (pending, approved, rejected, started) and hand-overs (open, "
                  "resolved: jobs that ended with exit 1, with the reason). Filter by status or kind.")
def queue_list(status: str = "", kind: str = "") -> Dict[str, Any]:
    items = queue.list_items(status=status, kind=kind)
    return {"items": items, "count": len(items)}


@tool(description="One queue item in full (its command, reason, decision, job).")
def queue_get(item_id: str) -> Dict[str, Any]:
    return queue.read(item_id)


@tool(description="Start an APPROVED queue item as a job (refused unless a human approved it and its command is "
                  "unchanged). Returns the job id: follow it with job_status / job_result.")
def queue_run(item_id: str) -> Dict[str, Any]:
    item, args = queue.approved_args(item_id)
    started = _start(item["action"], args, item["resource"], meta={"queue_item": item_id})
    queue.mark_started(item_id, started["job_id"])
    return dict(started, queue_item=item_id)


# --- judgement (step 5.7): the client's model judges, the pipeline records and bounds ------------

from mcp.server.mcpserver.utilities.types import Image  # noqa: E402

from agent.core import reviews  # noqa: E402

# What the concept checks can't see (docs/generation.md "What the checks can't see yet").
CONCEPT_QUESTIONS = (
    "Is it the object that was asked for (not a different object)?",
    "Is it a shaded 3D-looking object, not a flat 2D drawing or an icon (flat art breaks image-to-3D)?",
    "Is there any text, logo or watermark in it (there must be none)?",
    "Does it match the game's style?",
)
BANNER_QUESTIONS = (
    "Does the hero stand out from the background, at thumbnail size?",
    "Do the headline line breaks read naturally (not split mid-phrase)?",
    "Does it look like this game, and not like another game's banner?",
    "Would a UA lead ship it: CTA readable, nothing awkwardly cropped or overlapping?",
)


def _image(path: Path):
    if path.stat().st_size > RULES.judgement.max_image_bytes:
        raise ValueError("%s is %.1f MB, over the %.1f MB review limit" % (
            _rel(path), path.stat().st_size / 1e6, RULES.judgement.max_image_bytes / 1e6))
    return Image(path=path)


@tool(description="Look at a concept image the way an art director would. Returns the image, its mask and the "
                  "concept checks' results, plus the questions no check can answer (wrong object, flat 2D drawing, "
                  "text or logo, off-style). Judge them yourself, then call record_review; if it's wrong, "
                  "retry_concept with a better subject.")
def review_concept(concept_run: str) -> list:
    run = _inside(concept_run, "out/gen/runs")
    rep = _report(run / "concept.report.json")
    facts = {"run": _rel(run), "status": rep.get("status"),
             "checks": [{"id": r["id"], "passed": r["passed"], "message": r["message"]} for r in rep.get("results") or []],
             "prompt": (_report(run / "manifest.json").get("params") or {}).get("prompt"),
             "questions": list(CONCEPT_QUESTIONS),
             "earlier_reviews": reviews.list_reviews(_rel(run))}
    out = [json.dumps(facts, indent=1), _image(run / "image.png")]
    if (run / "mask.png").is_file():
        out.append(_image(run / "mask.png"))
    return out


@tool(description="Look at a rendered banner: its contact sheet (key frames decoded from the MP4) and its report, "
                  "plus the questions the 17 checks can't answer (does the hero pop, natural line breaks, does it "
                  "look like this game). Judge, then call record_review.")
def review_banner(folder: str) -> list:
    d = _inside(folder, "out")
    rep = _report(d / "banner.report.json")
    facts = {"folder": _rel(d), "status": rep.get("status"), "summary": rep.get("summary"),
             "failing": [{"id": r["id"], "message": r["message"]} for r in rep.get("results") or [] if not r["passed"]],
             "questions": list(BANNER_QUESTIONS), "earlier_reviews": reviews.list_reviews(_rel(d))}
    return [json.dumps(facts, indent=1), _image(d / "sheet.png")]


@tool(description="Return any PNG the pipeline wrote under out/ or a kit image under banners/kits/, to look at.")
def review_image(path: str) -> list:
    p = _inside(path, "out", "banners/kits")
    if p.suffix.lower() != ".png":
        raise ValueError("only PNG images: %s" % path)
    return [_rel(p), _image(p)]


@tool(description="Record your judgement of something you reviewed (target: a concept run, a banner folder or an "
                  "image path): verdict good, reject or unsure, and notes saying what you saw. Recorded for humans in "
                  "out/agent/reviews/; it gates nothing by itself.")
def record_review(target: str, verdict: str, notes: str) -> Dict[str, Any]:
    return reviews.record(target, verdict, notes)


@tool(description="Try a new subject for a concept you rejected (or one the repair loop handed over), keeping its "
                  "asset class and style. Bounded: at most judgement.max_prompt_changes new prompts from one original "
                  "concept (conventions/agent.yaml), each recorded with its reason; past that a human decides. "
                  "A GPU job.")
def retry_concept(previous_run: str, subject: str, asset_class: str, style: str, reason: str) -> Dict[str, Any]:
    if not reason.strip():
        raise ValueError("say why the previous concept was wrong")
    prev = _rel(_inside(previous_run, "out/gen/runs"))
    root = reviews.root_of(prev)
    if reviews.budget_left(root, RULES.judgement.max_prompt_changes) <= 0:
        raise ValueError("%d prompt change(s) already made from %s (max %d): a human decides what next"
                         % (RULES.judgement.max_prompt_changes, root, RULES.judgement.max_prompt_changes))
    started = gen_concept(subject, asset_class, style)
    data = reviews.add_change(root, subject, reason, started["job_id"], RULES.judgement.max_prompt_changes)
    return dict(started, root=root, prompt_changes=len(data["changes"]),
                left=RULES.judgement.max_prompt_changes - len(data["changes"]))


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
