"""Run one job to the end: `artpipe agent job-run JOB_ID` (detached, started by the MCP server).

queued: wait for the job's resource (the `gpu` lock for anything that runs
ComfyUI: one generation at a time, first come first served), unless the job
is cancelled while it waits. running: the artpipe command, its output
streaming into the job's logs, killed (with its whole process tree) at the
job's timeout. done: the exit code and its meaning in job.json. The lock is
released however the job ends; a runner killed outright leaves a stale lock
that the next job takes over (its holder's pid is dead).

Stdlib only, Python 3.9 (runs under mayapy like every artpipe command).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from agent.core import commands
from agent.core import jobs
from validate.core import locks

POLL_S = 0.5


def run(job_id: str, jobs_dir: Path = jobs.JOBS, locks_dir: Path = jobs.LOCKS) -> int:
    rec = jobs.read(job_id, jobs_dir)
    if rec["state"] != "queued":
        return 2
    with (jobs.job_dir(job_id, jobs_dir) / "runner.pid").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"pid": os.getpid(), "at": jobs.now()}) + "\n")
    lock = jobs.lock_path(rec["resource"], locks_dir) if rec["resource"] == "gpu" else None
    try:
        if lock is not None:
            while True:
                rec = jobs.read(job_id, jobs_dir)
                if rec.get("cancel_requested"):
                    rec.update(state="cancelled", finished=jobs.now(), meaning="cancelled while queued")
                    jobs.write(rec, jobs_dir)
                    return 2
                if jobs.first_in_line(rec, jobs_dir) and locks.try_lock(lock, job_id):
                    break
                time.sleep(POLL_S)
        rec = jobs.read(job_id, jobs_dir)
        rec.update(state="running", started=jobs.now())
        if rec["resource"] == "gpu":
            from gen import comfy
            rec["comfy_log_offset"] = comfy.log_size()   # progress reads only this job's bars (#44)
        jobs.write(rec, jobs_dir)
        d = jobs.job_dir(job_id, jobs_dir)
        code, timed_out = commands.run_to_files(rec["args"], rec["timeout_s"], d / "stdout.log", d / "stderr.log")
        rec = jobs.read(job_id, jobs_dir)
        if rec["state"] == "cancelled":       # job_cancel wrote it while the command was being killed
            return 2
        meaning = "timed out" if timed_out else commands.EXIT_MEANING.get(code, "unknown exit code")
        rec.update(state="done", finished=jobs.now(), exit_code=code, timed_out=timed_out, meaning=meaning)
        jobs.write(rec, jobs_dir)
        if code == 1 and jobs_dir == jobs.JOBS:
            # Handed over (or failed with nothing to fix): a human gets it in the queue.
            from agent.core import queue
            out_text = (d / "stdout.log").read_text(encoding="utf-8", errors="replace")
            err_text = (d / "stderr.log").read_text(encoding="utf-8", errors="replace")
            queue.handover(rec, queue.handover_reason(out_text, err_text))
        return code
    finally:
        if lock is not None:
            locks.release(lock, job_id)
