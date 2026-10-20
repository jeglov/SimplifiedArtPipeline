"""gen.comfy.session and progress: shared use of the pipeline's ComfyUI (backlog #45, #44). Fakes, <1 s."""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys

import pytest

from gen import comfy
from gen.core import ToolError
from gen.core import rules as gr
from validate.core import locks

RULES = gr.load().comfyui


class Fake:
    """Stands in for the server process: start/stop counters, a stats() that says up or not.

    Like the real start(), a start records the server in work/server.json (with a live pid:
    this test process); stop() removes it.
    """

    def __init__(self, monkeypatch, up=False, work=None):
        self.up, self.starts, self.stops, self.work = up, 0, 0, work
        monkeypatch.setattr(comfy, "start", self.start)
        monkeypatch.setattr(comfy, "stop", self.stop)
        monkeypatch.setattr(comfy.Client, "stats", lambda c: {"system": {}} if self.up else None)

    def start(self, rules, env=None, work=None):
        if self.up:
            return False
        self.up, self.starts = True, self.starts + 1
        if work is not None:
            (work / "server.json").write_text(json.dumps({"pid": os.getpid(), "port": 8188}), encoding="utf-8")
        return True

    def stop(self, work=None, port=None):
        self.up, self.stops = False, self.stops + 1
        if work is not None and (work / "server.json").exists():
            (work / "server.json").unlink()
        return True


def test_a_session_starts_and_stops_its_server(monkeypatch, tmp_path):
    f = Fake(monkeypatch)
    with comfy.session(RULES, work=tmp_path) as started:
        assert started and f.up and len(comfy.live_users(tmp_path)) == 1
    assert (f.starts, f.stops) == (1, 1) and comfy.live_users(tmp_path) == []


def test_nested_sessions_stop_once_at_the_outermost_exit(monkeypatch, tmp_path):
    f = Fake(monkeypatch)
    with comfy.session(RULES, work=tmp_path, ensure_running=False):
        assert not f.up                                       # the outer one doesn't start it
        for _ in range(3):                                    # three attempts of a repair loop
            with comfy.session(RULES, work=tmp_path):
                assert f.up
            assert f.up and f.stops == 0                      # the outer registration holds it
    assert (f.starts, f.stops) == (1, 1)


def test_another_live_user_keeps_it_running(monkeypatch, tmp_path):
    f = Fake(monkeypatch)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with comfy.session(RULES, work=tmp_path):
            (tmp_path / "users" / "other.json").write_text(json.dumps({"pid": other.pid}), encoding="utf-8")
        assert f.stops == 0 and f.up
    finally:
        other.kill()
        other.wait()
    # The other process is gone: the next session ignores (and removes) its registration.
    with comfy.session(RULES, work=tmp_path):
        pass
    assert f.stops == 1 and not (tmp_path / "users" / "other.json").exists()


@pytest.mark.parametrize("how", ["keep_server", "kept_record"])
def test_a_kept_server_is_not_stopped(monkeypatch, tmp_path, how):
    # kept_record: a server `gen server start` left running (up, recorded with keep).
    f = Fake(monkeypatch, up=(how == "kept_record"))
    if how == "kept_record":
        (tmp_path / "server.json").write_text(json.dumps({"pid": 1, "port": 8188, "keep": True}), encoding="utf-8")
    with comfy.session(RULES, work=tmp_path, keep_server=(how == "keep_server")):
        pass
    assert f.stops == 0


def test_a_server_already_running_before_is_left_alone(monkeypatch, tmp_path):
    f = Fake(monkeypatch, up=True)
    with comfy.session(RULES, work=tmp_path) as started:
        assert not started
    assert f.stops == 0


def test_stopped_even_when_the_block_raises(monkeypatch, tmp_path):
    f = Fake(monkeypatch)
    with pytest.raises(RuntimeError):
        with comfy.session(RULES, work=tmp_path):
            raise RuntimeError("a bug in the run")
    assert f.stops == 1 and comfy.live_users(tmp_path) == []


def test_a_start_held_by_a_live_process_times_out(monkeypatch, tmp_path):
    Fake(monkeypatch)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (tmp_path / "start.lock").write_text(json.dumps({"owner": "o", "pid": other.pid}), encoding="utf-8")
        quick = dataclasses.replace(RULES, startup_timeout_s=1)
        monkeypatch.setattr(locks, "acquire", lambda path, owner, timeout_s: False)
        with pytest.raises(ToolError, match="starting ComfyUI"):
            with comfy.session(quick, work=tmp_path):
                pass
    finally:
        other.kill()
        other.wait()


def test_progress_reads_the_latest_bar_after_an_offset(tmp_path):
    log = tmp_path / "server.log"
    log.write_bytes(b"got prompt\r\n 50%|#####     | 4/8 [00:03<00:03,  1.1it/s]\r100%|##########| 8/8 [00:07<00:00]\n")
    p = comfy.progress(log)
    assert (p["percent"], p["step"], p["steps"]) == (100, 8, 8)
    offset = comfy.log_size(log)
    assert comfy.progress(log, since=offset) is None              # nothing new since the job started
    with log.open("ab") as fh:
        fh.write(b"got prompt\n 25%|##5       | 2/8 [00:01<00:05,  1.2it/s]")
    assert comfy.progress(log, since=offset)["step"] == 2
    assert comfy.progress(log, since=10 ** 9)["step"] == 2      # a rewritten log: read from the start
    assert comfy.progress(tmp_path / "missing.log") is None
