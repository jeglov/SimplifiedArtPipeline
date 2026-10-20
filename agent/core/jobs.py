"""Jobs: a long artpipe command (a render, a generation, a batch) as a record on disk.

    out/agent/jobs/<job_id>/job.json     the record (conventions/agent_job.schema.json)
                            stdout.log   what the command printed
                            stderr.log

A tool that takes longer than a call should starts a job and returns its id
at once; the agent then asks for its status (progress) and its result. The
work runs in a detached runner process (`artpipe agent job-run ID`), so a job
survives the MCP server and the client restarting, and a status read never
blocks.

States: queued -> running -> done (the command exited; its exit code says
pass / failed / tool error) | cancelled | lost (the runner died without
finishing, e.g. the machine slept and was killed).

Resources: a job that needs the GPU (anything that runs ComfyUI) waits for
the `gpu` lock, so the agent can start ten generations and they run one at a
time, first come first served (backlog #45). Locks are files holding the
holder's pid; a lock whose holder is dead is stale and is taken over.

Stdlib only, Python 3.9.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from agent.core.commands import ROOT
from validate.core.locks import now, pid_alive

JOBS = ROOT / "out" / "agent" / "jobs"
LOCKS = ROOT / "out" / "agent" / "locks"
SCHEMA_VERSION = "1.0"
STATES = ("queued", "running", "done", "cancelled", "lost")
FINAL = ("done", "cancelled", "lost")
RESOURCES = ("cpu", "gpu")


def new_id(tool: str) -> str:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
    return "%s-%s-%s" % (tool.replace("_", "-"), stamp, uuid.uuid4().hex[:6])


def job_dir(job_id: str, jobs: Path = JOBS) -> Path:
    if not job_id or any(c in job_id for c in "/\\:.") or job_id != job_id.strip():
        raise ValueError("not a job id: %r" % job_id)
    return jobs / job_id


def write(record: Mapping[str, Any], jobs: Path = JOBS) -> None:
    """Atomic write: a reader never sees half a record."""
    d = job_dir(record["id"], jobs)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "job.json.tmp"
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    os.replace(str(tmp), str(d / "job.json"))


def read(job_id: str, jobs: Path = JOBS) -> Dict[str, Any]:
    path = job_dir(job_id, jobs) / "job.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise KeyError("no job %s" % job_id)


def create(tool: str, args: Sequence[str], resource: str = "cpu", timeout_s: int = 3600,
           meta: Optional[Mapping[str, Any]] = None, jobs: Path = JOBS) -> Dict[str, Any]:
    if resource not in RESOURCES:
        raise ValueError("resource must be one of %s" % (RESOURCES,))
    record = {"schema_version": SCHEMA_VERSION, "id": new_id(tool), "tool": tool, "args": [str(a) for a in args],
              "resource": resource, "timeout_s": int(timeout_s), "state": "queued", "created": now(),
              "started": None, "finished": None, "exit_code": None, "meaning": None,
              "timed_out": False, "cancel_requested": False, "meta": dict(meta or {})}
    write(record, jobs)
    return record


def runner_pid(job_id: str, jobs: Path = JOBS) -> Optional[int]:
    """The runner's own pid (runner.pid, written by the runner), else the pid it was spawned as (spawn.json).

    Each file has one writer, so start() and the runner never overwrite each
    other's job.json (probed: with WMI's ~1 s spawn, start() writing the record
    after spawning put a job that was already running back to `queued`).
    """
    d = job_dir(job_id, jobs)
    for name in ("runner.pid", "spawn.json"):
        try:
            return int(json.loads((d / name).read_text(encoding="utf-8"))["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


NO_RUNNER_GRACE_S = 120     # a queued job with no runner pid at all after this never got one


def _age_s(stamp: str) -> float:
    try:
        then = _dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=_dt.timezone.utc)
    except (TypeError, ValueError):
        return 0.0
    return (_dt.datetime.now(_dt.timezone.utc) - then).total_seconds()


def refresh(record: Dict[str, Any], jobs: Path = JOBS) -> Dict[str, Any]:
    """A job whose runner died without finishing is `lost` (written back, so it's said once).

    So is a queued job that never got a runner (no pid after NO_RUNNER_GRACE_S:
    its server crashed between writing the record and spawning the runner).
    """
    pid = runner_pid(record["id"], jobs)
    never = pid is None and record["state"] == "queued" and _age_s(record["created"]) > NO_RUNNER_GRACE_S
    if record["state"] in ("queued", "running") and ((pid and not pid_alive(pid)) or never):
        record = dict(record, state="lost", finished=now(),
                      meaning="the runner process ended without finishing the job")
        write(record, jobs)
    return record


def list_jobs(jobs: Path = JOBS) -> List[Dict[str, Any]]:
    out = []
    if jobs.is_dir():
        for d in sorted(jobs.iterdir()):
            if (d / "job.json").is_file():
                try:
                    out.append(refresh(read(d.name, jobs), jobs))
                except KeyError:
                    continue
    return sorted(out, key=lambda r: r["created"])


def tail(path: Path, lines: int = 20) -> List[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    # A progress bar redraws with \r: keep the last state of each line.
    return [ln.split("\r")[-1] for ln in text.splitlines() if ln.split("\r")[-1].strip()][-lines:]


# --- locks ---------------------------------------------------------------------

def lock_path(name: str, locks: Path = LOCKS) -> Path:
    return locks / ("%s.lock" % name)


def first_in_line(record: Mapping[str, Any], jobs: Path = JOBS) -> bool:
    """FIFO for a resource: no earlier job of the same resource is still waiting."""
    for other in list_jobs(jobs):
        if other["id"] == record["id"]:
            return True
        if other["resource"] == record["resource"] and other["state"] == "queued" \
                and not other.get("cancel_requested") and pid_alive(runner_pid(other["id"], jobs)):
            return False
    return True


# --- control (used by the MCP server and the tests) ------------------------------

def in_job_object() -> bool:
    """True if this process runs inside a Windows Job Object (IsProcessInJob)."""
    if os.name != "nt":
        return False
    import ctypes
    result = ctypes.c_int(0)
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    ok = kernel32.IsProcessInJob(ctypes.c_void_p(kernel32.GetCurrentProcess()), None, ctypes.byref(result))
    return bool(ok) and bool(result.value)


def _spawn_via_wmi(argv: Sequence[str]) -> int:
    """Create the process through WMI (Win32_Process.Create): the WMI service starts it, outside any
    Job Object of ours, with the user's own environment. Returns its pid."""
    import subprocess

    def ps_quote(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"
    line = " ".join('"%s"' % a if " " in a else a for a in argv)
    script = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
              "@{CommandLine=%s; CurrentDirectory=%s}; if ($r.ReturnValue -ne 0) { exit 3 }; "
              "Write-Output $r.ProcessId" % (ps_quote(line), ps_quote(str(ROOT))))
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    if out.returncode != 0 or not out.stdout.strip().isdigit():
        raise OSError("could not start the job runner through WMI: %s" % (out.stderr.strip() or out.stdout))
    return int(out.stdout.strip())


def spawn_runner(record: Dict[str, Any]) -> int:
    """Start `artpipe agent job-run ID` so that it outlives the server that started it. Returns its pid.

    Probed: the MCP SDK's stdio client puts the server in a Windows Job Object
    with KILL_ON_JOB_CLOSE, and closing the session kills every member. A
    detached child died with it (the job came back `lost`), and so did one
    started with CREATE_BREAKAWAY_FROM_JOB (CreateProcess accepted the flag,
    the runner still died: nested jobs). So inside a job the runner is
    created by WMI (Win32_Process.Create: the WMI service starts it, outside
    our job, ~1 s); outside a job a detached child is enough.
    """
    import subprocess
    from agent.core.commands import argv_for
    argv = argv_for(["agent", "job-run", record["id"]])
    if os.name != "nt":
        record["spawn"] = "detached"
        return subprocess.Popen(argv, cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True).pid
    if in_job_object():
        record["spawn"] = "wmi"
        return _spawn_via_wmi(argv)
    record["spawn"] = "detached"
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    return subprocess.Popen(argv, cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=flags).pid


def start(tool: str, args: Sequence[str], resource: str = "cpu", timeout_s: int = 3600,
          meta: Optional[Mapping[str, Any]] = None, jobs: Path = JOBS) -> Dict[str, Any]:
    from agent.core.commands import check_args
    check_args(args)                     # refuse before a record exists
    record = create(tool, args, resource, timeout_s, meta, jobs)
    if jobs == JOBS:
        info: Dict[str, Any] = {"id": record["id"]}
        info["pid"] = spawn_runner(info)
        info["at"] = now()
        with (job_dir(record["id"], jobs) / "spawn.json").open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(info) + "\n")
        record = dict(record, runner_pid=info["pid"], spawn=info.get("spawn"))   # for the caller only
    return record


def queue_position(record: Mapping[str, Any], jobs: Path = JOBS) -> Optional[int]:
    """1 = next to run, among queued jobs of the same resource; None if not queued."""
    if record["state"] != "queued":
        return None
    waiting = [r for r in list_jobs(jobs) if r["resource"] == record["resource"] and r["state"] == "queued"
               and (r["id"] == record["id"] or pid_alive(runner_pid(r["id"], jobs)))]
    ids = [r["id"] for r in waiting]
    return ids.index(record["id"]) + 1 if record["id"] in ids else None


def status(job_id: str, jobs: Path = JOBS, extra_progress: Optional[List[str]] = None) -> Dict[str, Any]:
    rec = refresh(read(job_id, jobs), jobs)
    d = job_dir(job_id, jobs)
    out = {k: rec[k] for k in ("id", "tool", "state", "resource", "created", "started", "finished", "exit_code",
                               "meaning", "timed_out")}
    out["command"] = "artpipe " + " ".join(rec["args"])
    out["queue_position"] = queue_position(rec, jobs)
    out["log_tail"] = tail(d / "stdout.log", 8) + tail(d / "stderr.log", 8)
    if extra_progress:
        out["progress"] = extra_progress
    return out


def result(job_id: str, jobs: Path = JOBS, limit: int = 20000) -> Dict[str, Any]:
    """The finished job: exit code, meaning, what it printed (capped). Not finished: its status."""
    rec = refresh(read(job_id, jobs), jobs)
    d = job_dir(job_id, jobs)
    out = status(job_id, jobs)
    if rec["state"] in FINAL:
        for name in ("stdout", "stderr"):
            try:
                text = (d / ("%s.log" % name)).read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            out[name] = text[-limit:]
    return out


def cancel(job_id: str, jobs: Path = JOBS) -> Dict[str, Any]:
    """Cancel a queued or running job: its runner (and the command under it) is killed."""
    from agent.core.commands import kill_tree
    rec = refresh(read(job_id, jobs), jobs)
    if rec["state"] in FINAL:
        return status(job_id, jobs)
    rec.update(cancel_requested=True)
    write(rec, jobs)
    pid = runner_pid(job_id, jobs)
    if pid:
        # Running: the command dies with it. Queued: it was only waiting for its lock. A
        # lock the runner held becomes stale (dead pid) and the next job takes it over.
        kill_tree(pid)
    rec = read(job_id, jobs)
    if rec["state"] not in FINAL:
        rec.update(state="cancelled", finished=now(), meaning="cancelled by request")
        write(rec, jobs)
    return status(job_id, jobs)
