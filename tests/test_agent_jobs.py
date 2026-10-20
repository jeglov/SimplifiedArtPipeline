"""agent.core.jobs + agent.runner on real detached runners (`artpipe agent job-run`). ~40 s.

Every job here is `artpipe py -c ...` (no Maya scene, no GPU): what's under test is the job
machinery: states, logs, timeouts, cancellation, the GPU queue, lost runners, stale locks.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from agent.core import commands, jobs
from validate.core import locks


def wait_for(job_id, states, timeout=60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = jobs.status(job_id)
        if st["state"] in states:
            return st
        time.sleep(0.3)
    raise AssertionError("job %s never reached %s: %s" % (job_id, states, jobs.status(job_id)))


def py(code, resource="cpu", timeout_s=120):
    return jobs.start("selftest", ["py", "-c", code], resource, timeout_s)


def test_a_job_runs_to_done_with_its_output():
    rec = py("print('hello from a job')")
    assert rec["state"] == "queued" and rec["runner_pid"] and rec["spawn"] in ("detached", "wmi")
    st = wait_for(rec["id"], jobs.FINAL)
    assert (st["state"], st["exit_code"], st["meaning"]) == ("done", 0, "pass")
    res = jobs.result(rec["id"])
    assert "hello from a job" in res["stdout"] and res["command"].startswith("artpipe py -c")


def test_exit_codes_keep_their_meaning():
    rec = py("import sys; sys.exit(1)")
    st = wait_for(rec["id"], jobs.FINAL)
    assert st["exit_code"] == 1 and "failed its checks" in st["meaning"]


def test_a_job_past_its_timeout_is_killed():
    rec = py("import time; time.sleep(60)", timeout_s=2)
    st = wait_for(rec["id"], jobs.FINAL, timeout=30)
    assert st["timed_out"] and st["exit_code"] == 2 and st["meaning"] == "timed out"


def _mayapy_running(marker):
    ps = subprocess.run(["powershell", "-NoProfile", "-Command",
                         "Get-CimInstance Win32_Process -Filter \"Name='mayapy.exe'\" | "
                         "Select-Object -ExpandProperty CommandLine"],
                        stdout=subprocess.PIPE, universal_newlines=True).stdout
    return marker in ps


def test_cancel_kills_a_running_job_and_everything_under_it():
    marker = "cancel_probe_%d" % int(time.time() * 1000)
    rec = py("import time; time.sleep(60)  # " + marker)
    wait_for(rec["id"], ("running",))
    time.sleep(1.0)                                            # the command itself is up
    st = jobs.cancel(rec["id"])
    assert st["state"] == "cancelled"
    time.sleep(1.0)
    assert not locks.pid_alive(jobs.runner_pid(rec["id"])) and not _mayapy_running(marker)
    assert jobs.cancel(rec["id"])["state"] == "cancelled"      # cancelling twice is harmless


def test_gpu_jobs_run_one_at_a_time_first_come_first_served():
    a = py("import time; time.sleep(4)", resource="gpu")
    time.sleep(0.3)
    b = py("import time; time.sleep(1)", resource="gpu")
    wait_for(a["id"], ("running",))
    time.sleep(1.5)
    sb = jobs.status(b["id"])
    assert sb["state"] == "queued" and sb["queue_position"] == 1
    fa, fb = wait_for(a["id"], jobs.FINAL), wait_for(b["id"], jobs.FINAL)
    assert fa["exit_code"] == fb["exit_code"] == 0
    assert fb["started"] >= fa["finished"]                    # never overlapping
    assert locks.holder(jobs.lock_path("gpu")) is None          # released


def test_cancel_a_job_waiting_in_the_queue():
    a = py("import time; time.sleep(4)", resource="gpu")
    time.sleep(0.3)
    b = py("print('never')", resource="gpu")
    wait_for(a["id"], ("running",))
    assert jobs.cancel(b["id"])["state"] == "cancelled"
    wait_for(a["id"], jobs.FINAL)
    time.sleep(1.0)
    assert jobs.status(b["id"])["state"] == "cancelled" and "never" not in jobs.result(b["id"]).get("stdout", "")


def test_a_runner_that_dies_leaves_a_lost_job():
    rec = py("import time; time.sleep(60)")
    wait_for(rec["id"], ("running",))
    commands.kill_tree(jobs.runner_pid(rec["id"]))             # the machine went to sleep, someone killed it
    time.sleep(1.0)
    st = jobs.status(rec["id"])
    assert st["state"] == "lost" and "without finishing" in st["meaning"]


def test_a_stale_gpu_lock_is_taken_over():
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    lock = jobs.lock_path("gpu")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"owner": "crashed-job", "pid": dead.pid}), encoding="utf-8")
    rec = py("print('ran')", resource="gpu")
    assert wait_for(rec["id"], jobs.FINAL)["exit_code"] == 0


def test_cmd_syntax_is_refused_before_a_job_exists():
    before = len(jobs.list_jobs())
    with pytest.raises(ValueError, match="cmd.exe"):
        py("print(1) & echo pwned")
    assert len(jobs.list_jobs()) == before


def test_job_ids_are_checked():
    for bad in ("../x", "a/b", "C:x", "", " x"):
        with pytest.raises(ValueError):
            jobs.job_dir(bad)
    with pytest.raises(KeyError):
        jobs.read("selftest-20000101T000000-nothere")


def test_a_queued_job_that_never_got_a_runner_is_lost_after_the_grace(tmp_path):
    rec = jobs.create("selftest", ["py", "-c", "pass"], jobs=tmp_path)          # no spawn: a crashed server
    assert jobs.refresh(jobs.read(rec["id"], tmp_path), tmp_path)["state"] == "queued"   # inside the grace
    old = jobs.read(rec["id"], tmp_path)
    old["created"] = "2000-01-01T00:00:00.000000Z"
    jobs.write(old, tmp_path)
    assert jobs.refresh(jobs.read(rec["id"], tmp_path), tmp_path)["state"] == "lost"


def test_the_queue_position_ignores_dead_and_other_resources(tmp_path):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    a = jobs.create("selftest", ["py"], "gpu", jobs=tmp_path)
    (tmp_path / a["id"] / "spawn.json").write_text(json.dumps({"pid": dead.pid}), encoding="utf-8")
    jobs.create("selftest", ["py"], "cpu", jobs=tmp_path)
    c = jobs.create("selftest", ["py"], "gpu", jobs=tmp_path)
    (tmp_path / c["id"] / "spawn.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    assert jobs.queue_position(jobs.read(c["id"], tmp_path), tmp_path) == 1


def test_job_records_match_the_schema():
    import jsonschema
    from validate.core.conventions import DEFAULT_DIR
    schema = json.loads((DEFAULT_DIR / "agent_job.schema.json").read_text(encoding="utf-8"))
    # Records written by this version (the starter leaves spawn.json next to them).
    recs = [r for r in jobs.list_jobs() if (jobs.job_dir(r["id"]) / "spawn.json").is_file()]
    assert recs
    for r in recs[-20:]:
        jsonschema.validate(r, schema)


def test_first_in_line_waits_only_for_earlier_live_jobs_of_the_same_resource(tmp_path):
    # The live FIFO test above needs real runners; this one is pure, so the mutation pass sees it.
    def queued(resource, pid=None):
        rec = jobs.create("selftest", ["py"], resource, jobs=tmp_path)
        if pid is not None:
            (tmp_path / rec["id"] / "spawn.json").write_text(json.dumps({"pid": pid}), encoding="utf-8")
        time.sleep(0.01)                                   # distinct `created` stamps
        return rec
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    first = queued("gpu", os.getpid())
    queued("cpu", os.getpid())                             # another resource
    queued("gpu", dead.pid)                                # a dead runner
    last = queued("gpu", os.getpid())
    assert jobs.first_in_line(jobs.read(first["id"], tmp_path), tmp_path)
    assert not jobs.first_in_line(jobs.read(last["id"], tmp_path), tmp_path)
    old = jobs.read(first["id"], tmp_path)
    old["cancel_requested"] = True                         # a cancelled job doesn't hold the line
    jobs.write(old, tmp_path)
    assert jobs.first_in_line(jobs.read(last["id"], tmp_path), tmp_path)
