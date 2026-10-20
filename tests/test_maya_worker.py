"""The persistent Maya worker (backlog #1): same reports as the cold CLI, much faster, restarts itself.

Marker maya (needs mayapy and the fixture scenes: `artpipe fixtures`). ~1-2 min: one cold CLI run,
one worker boot, then warm requests.
"""
from __future__ import annotations

import json
import socket
import subprocess
import time
from pathlib import Path

import pytest

from agent.core import commands, maya_worker
from agent.core import ToolError

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "out" / "fixtures"
pytestmark = [pytest.mark.maya,
              pytest.mark.skipif(not (FIXTURES / "SM_Messy.ma").is_file(),
                                 reason="fixture scenes missing: artpipe fixtures")]


@pytest.fixture(scope="module")
def worker():
    maya_worker.stop()
    t = time.time()
    info = maya_worker.start(idle_s=300)
    boot = time.time() - t
    yield info, boot
    maya_worker.stop()


def rel(p):
    return Path(p).relative_to(ROOT).as_posix() if Path(p).is_absolute() else p


def test_the_worker_writes_the_same_report_as_the_cold_cli(worker, tmp_path):
    scene = rel(FIXTURES / "SM_Messy.ma")
    cold_dir, warm_dir = tmp_path / "cold", tmp_path / "warm"
    t = time.time()
    cold = commands.run(["validate", scene, "--class", "prop_small", "--out", str(cold_dir)], timeout_s=300)
    cold_s = time.time() - t
    warm = maya_worker.call("validate", [scene, "--class", "prop_small", "--out", str(warm_dir)])
    assert cold.exit_code == warm["exit_code"] == 1                  # SM_Messy fails as it should
    name = "SM_Messy.ma.report.json"
    assert (cold_dir / name).read_bytes() == (warm_dir / name).read_bytes()
    assert warm["seconds"] < cold_s / 3, "warm %.1f s vs cold %.1f s" % (warm["seconds"], cold_s)


def test_fix_and_export_through_the_worker_match_the_cold_cli(worker, tmp_path):
    scene = rel(FIXTURES / "SM_Messy.ma")
    args = lambda d: [scene, "--class", "prop_small", "--fix", "--fixed-dir", str(d / "fixed"),
                      "--export", str(d / "fbx"), "--out", str(d)]
    cold = commands.run(["validate"] + args(tmp_path / "cold"), timeout_s=300)
    warm = maya_worker.call("validate", args(tmp_path / "warm"))
    assert cold.exit_code == warm["exit_code"] == 0
    # The scene and the FBX: identical bytes. The reports name their own output folders
    # (cold/ vs warm/), so they are compared with that one path segment normalised.
    for sub in ("fixed/SM_Messy.ma", "fbx/SM_Messy.fbx"):
        assert (tmp_path / "cold" / sub).read_bytes() == (tmp_path / "warm" / sub).read_bytes(), sub
    for sub in ("SM_Messy.ma.report.json", "fbx/SM_Messy.fbx.report.json"):
        a = (tmp_path / "cold" / sub).read_text(encoding="utf-8").replace("/cold/", "/X/")
        b = (tmp_path / "warm" / sub).read_text(encoding="utf-8").replace("/warm/", "/X/")
        assert a == b, sub


def test_one_scene_doesnt_leak_into_the_next(worker, tmp_path):
    # Clean after messy, messy after clean: each request opens a fresh scene.
    out = []
    for scene in ("SM_Clean.ma", "SM_Messy.ma", "SM_Clean.ma"):
        r = maya_worker.call("validate", [rel(FIXTURES / scene), "--class", "prop_small", "--out",
                                          str(tmp_path / str(len(out)))])
        out.append(r["exit_code"])
    assert out == [0, 1, 0]
    a, b = (tmp_path / "0" / "SM_Clean.ma.report.json"), (tmp_path / "2" / "SM_Clean.ma.report.json")
    assert a.read_bytes() == b.read_bytes()


def test_list_checks(worker):
    reply = maya_worker.call("list_checks", [])
    reg = json.loads(reply["stdout"].strip().splitlines()[-1])
    modules = lambda d: len([m for m in (ROOT / "validate" / "maya" / d).glob("*.py") if not m.name.startswith("_")])
    assert len(reg["checks"]) == modules("checks") and len(reg["fixes"]) == modules("fixes")
    assert {"id", "severity", "fixable", "description"} == set(reg["checks"][0])


def test_a_bad_token_is_refused(worker):
    info, _ = worker
    with pytest.raises(ToolError, match="bad token"):
        maya_worker._send(dict(info, token="0" * 32), "ping", [], 5)


def test_argparse_errors_dont_kill_the_worker(worker):
    r = maya_worker.call("validate", ["--class"])
    assert r["exit_code"] == 2
    assert maya_worker.alive() is not None


def test_a_dead_worker_is_restarted_on_the_next_call(worker, tmp_path):
    info = maya_worker.record()
    commands.kill_tree(int(info["pid"]))
    time.sleep(1)
    assert maya_worker.alive() is None
    r = maya_worker.call("validate", [rel(FIXTURES / "SM_Clean.ma"), "--class", "prop_small", "--out", str(tmp_path)])
    assert r["exit_code"] == 0 and maya_worker.record()["pid"] != info["pid"]
