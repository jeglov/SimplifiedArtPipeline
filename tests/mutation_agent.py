"""Mutation pass over the agent layer (Phase 5).

    artpipe py tests/mutation_agent.py

Plants one bug at a time in the agent's safety-critical logic (the approval
queue, the path guard, the argument filter and quoting, jobs and locks, the
ComfyUI session, the prompt budget), runs the agent tests that need no MCP
client (`-m "not agent and not render"`), restores the file, and lists the
survivors. Exit 1 if any survived. Bytecode caching is off (a stale .pyc once
hid a survivor). Not collected by pytest.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.core import jobs  # noqa: E402  (imported before any mutant is planted)
TESTS = ["tests/test_agent_queue.py", "tests/test_agent_core.py", "tests/test_agent_jobs.py", "tests/test_locks.py",
         "tests/test_comfy_session.py", "tests/test_agent_judgement.py", "tests/test_agent_safety.py",
         "-m", "not agent and not render"]
MUTANTS = [
    # the approval queue (5.6)
    ("agent/core/queue.py", 'if item["status"] != "approved":', 'if item["status"] not in ("approved", "pending"):',
     "pending items runnable"),
    ("agent/core/queue.py", 'if args_sha256(item["args"]) != item["args_sha256"]:', "if False:",
     "edited command accepted"),
    ("agent/core/queue.py", '    if rebuilt != item["args"]:', "    if False:", "edited parameters accepted"),
    ("agent/core/queue.py", '    if item["status"] != "pending":\n        raise ValueError("%s is already %s"',
     '    if item["status"] == "rejected":\n        raise ValueError("%s is already %s"', "re-approval after a run"),
    ("agent/core/queue.py", "    if not reason.strip():", "    if False:", "request without a reason"),
    ("agent/core/queue.py", '    if existing:\n        return existing[0]', '    if False:\n        return existing[0]',
     "duplicate hand-overs"),
    # the path guard and the argument filter (5.1, 5.4, 5.5)
    ("agent/core/safety.py", "        if p == base or base in p.parents:",
     "        if str(p).startswith(str(base)):", "string-prefix path check"),
    ("agent/core/commands.py", "if any(c in s for c in '&|<>^%\"\\r\\n'):", "if any(c in s for c in '&|<>'):",
     "% ^ \" newline allowed"),
    ("agent/core/commands.py", "inner = \" \".join('\"%s\"' % a for a in", "inner = \" \".join('%s' % a for a in",
     "arguments not quoted"),
    # jobs and locks (5.2)
    ("agent/core/jobs.py", "    never = pid is None and record[\"state\"] == \"queued\" and _age_s(record[\"created\"]) > NO_RUNNER_GRACE_S",
     "    never = False", "never-spawned jobs stay queued"),
    ("agent/core/jobs.py", "                and not other.get(\"cancel_requested\") and pid_alive(runner_pid(other[\"id\"], jobs)):",
     "                and False:", "FIFO ignored"),
    ("validate/core/locks.py", "            elif pid_alive(h.get(\"pid\")):\n                return False",
     "            elif h is not None:\n                return False", "stale locks never taken over"),
    ("validate/core/locks.py", "                if age < EMPTY_STALE_S:", "                if True:", "empty locks never stale"),
    # the ComfyUI session (#45)
    ("gen/comfy.py", "                if not keep_server and (started or not was_up or owned(work)) and not live_users(work) \\",
     "                if not keep_server and (started or not was_up or owned(work)) \\", "stops under another user"),
    ("gen/comfy.py", "                        and not kept(work):", "                        and True:", "stops a kept server"),
    # the prompt budget (5.7)
    ("agent/core/reviews.py", "    if len(data[\"changes\"]) >= max_changes:", "    if len(data[\"changes\"]) > max_changes:",
     "one prompt change too many"),
    ("agent/core/reviews.py", "                if change.get(\"run\") == run:\n                    return data[\"root\"]",
     "                if False:\n                    return data[\"root\"]", "a retry's retry gets a fresh budget"),
]


def _cancel_jobs_since(stamp):
    # A runner spawned under a mutant keeps the mutated code after the file is
    # restored (probed: one that never took over a stale GPU lock held the FIFO
    # for every later job). Kill every job the mutant's tests started.
    for record in jobs.list_jobs():
        if record["created"] >= stamp and record["state"] in ("queued", "running"):
            jobs.cancel(record["id"])


def main():
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    survived = []
    for path, old, new, what in MUTANTS:
        f = ROOT / path
        with open(f, encoding="utf-8", newline="") as fh:
            src = fh.read()
        if src.count(old) != 1:
            print("STALE    %-28s %s (pattern found %d times: update the mutant)" % (path, what, src.count(old)))
            survived.append(what)
            continue
        started = jobs.now()
        try:
            with open(f, "w", encoding="utf-8", newline="") as fh:
                fh.write(src.replace(old, new))
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"] + TESTS,
                               cwd=str(ROOT), capture_output=True, text=True, env=env)
        finally:
            with open(f, "w", encoding="utf-8", newline="") as fh:
                fh.write(src)
            _cancel_jobs_since(started)
        killed = r.returncode != 0
        print("%s %-28s %s" % ("KILLED  " if killed else "SURVIVED", path, what))
        if not killed:
            survived.append(what)
    print("survived:", survived)
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
