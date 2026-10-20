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

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from mcp.server.mcpserver import MCPServer

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


@server.tool(description="What this artpipe is: repo commit, the rules files (with sha256), the agent "
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


@server.tool(description="State of a job: queued (with its queue position), running (with the log tail and, "
                         "for GPU jobs, ComfyUI's step progress), done (exit code and meaning), cancelled or lost.")
def job_status(job_id: str) -> Dict[str, Any]:
    out = jobs.status(job_id)
    prog = _gpu_progress(job_id)
    if prog:
        out["comfy_progress"] = prog
    return out


@server.tool(description="A finished job's exit code, meaning and full output (capped). Before it finishes, "
                         "the same as job_status.")
def job_result(job_id: str) -> Dict[str, Any]:
    return jobs.result(job_id)


@server.tool(description="Cancel a queued or running job; its command and every process under it are killed.")
def job_cancel(job_id: str) -> Dict[str, Any]:
    return jobs.cancel(job_id)


@server.tool(description="Every job with its state, newest last. state filters (queued, running, done, "
                         "cancelled, lost).")
def job_list(state: str = "") -> Dict[str, Any]:
    rows = [{k: r[k] for k in ("id", "tool", "state", "resource", "created", "exit_code", "meaning")}
            for r in jobs.list_jobs() if not state or r["state"] == state]
    return {"jobs": rows, "count": len(rows)}


@server.tool(description="Render one UA banner (template x kit x placement x locale), check it, and with "
                         "repair=True fix and re-render it per conventions/banners.yaml. A job (~2-5 min). "
                         "Result: exit 0 pass / 1 handed over (repair.json says why) / 2 tool error; the banner "
                         "folder is out/banners/<kit>/<template>/<placement>/<locale>/.")
def banners_render(kit: str, template: str, placement: str = "ua_video_9x16", locale: str = "en",
                   repair: bool = True) -> Dict[str, Any]:
    args = ["banners", "render", kit, "--template", template, "--placement", placement, "--locale", locale]
    return _start("banners_render", args + (["--repair"] if repair else []))


@server.tool(description="Generate a concept image for an asset on the pinned ComfyUI and check it for "
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


@server.tool(description="List the checks (and fixes) you can run. domain: maya (scene checks + safe fixes), "
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


@server.tool(description="Run ONE Maya check on a scene (.ma/.mb/.fbx, repo-relative or absolute) for an asset "
                         "class (conventions/mesh_budgets.yaml). Returns that check's result: passed, severity, "
                         "message, metrics, offenders (component names). Warm Maya worker: well under a second.")
def run_check(file: str, asset_class: str, check_id: str) -> Dict[str, Any]:
    d = _call_dir("check")
    out = _maya([file, "--class", asset_class, "--checks", check_id, "--out", str(d)])
    rep = _report(d / (Path(file).name + ".report.json"))
    out.update(result=(rep.get("results") or [None])[0], tool_error=rep.get("tool_error"),
               report=_rel(d / (Path(file).name + ".report.json")))
    return out


@server.tool(description="Apply ONE safe fix to a scene and re-validate the fixed copy with every check. The "
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


@server.tool(description="Re-run banner checks on a rendered banner folder (out/banners/<kit>/<template>/"
                         "<placement>/<locale>), all of them or only check_ids (comma-separated). Returns the "
                         "report's status and each result. Writes banner.report.json in that folder.")
def banners_check(folder: str, check_ids: str = "") -> Dict[str, Any]:
    args = ["banners", "check", folder] + (["--checks", check_ids] if check_ids else [])
    out = commands.run(args, RULES.server.call_timeout_s)
    rep = _report((ROOT / folder if not Path(folder).is_absolute() else Path(folder)) / "banner.report.json")
    return dict(out.to_dict(), status=rep.get("status"), results=rep.get("results"))


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
