"""validate.core.locks: file locks with stale-holder takeover. Pure Python, <1 s (one helper subprocess)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from validate.core import locks


def dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_one_holder_at_a_time(tmp_path):
    lock = tmp_path / "x.lock"
    assert locks.try_lock(lock, "a")
    assert not locks.try_lock(lock, "b")                     # held by us, alive
    locks.release(lock, "b")                                 # not b's to release
    assert lock.exists()
    locks.release(lock, "a")
    assert not lock.exists() and locks.try_lock(lock, "b")


def test_a_dead_holders_lock_is_taken_over(tmp_path):
    lock = tmp_path / "x.lock"
    lock.write_text(json.dumps({"owner": "gone", "pid": dead_pid()}), encoding="utf-8")
    assert locks.try_lock(lock, "me")
    assert locks.holder(lock)["owner"] == "me"


def test_an_empty_lock_is_respected_then_stale(tmp_path):
    lock = tmp_path / "x.lock"
    lock.write_text("", encoding="utf-8")                     # being written by someone right now
    assert not locks.try_lock(lock, "me")
    old = time.time() - locks.EMPTY_STALE_S - 1               # boundary: older than the grace period
    os.utime(str(lock), (old, old))
    assert locks.try_lock(lock, "me")


def test_acquire_waits_then_gives_up(tmp_path):
    lock = tmp_path / "x.lock"
    assert locks.try_lock(lock, "a")
    t = time.time()
    assert not locks.acquire(lock, "b", timeout_s=0.5, poll_s=0.1)
    assert 0.4 < time.time() - t < 3


def test_pid_alive():
    assert locks.pid_alive(os.getpid()) and not locks.pid_alive(dead_pid())
    assert not locks.pid_alive(None) and not locks.pid_alive(0) and not locks.pid_alive(-5)
