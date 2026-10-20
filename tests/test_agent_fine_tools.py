"""Step 5.4: one check, one fix. The CLI filters (marker maya) and the MCP tools on the real server
(markers agent + maya). ~1 min (one worker boot, one 1 s banner render)."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from agent import cli as agent_cli
from agent.core import commands
from agent.core import rules as ar

ROOT = Path(__file__).resolve().parents[1]
MESSY = "out/fixtures/SM_Messy.ma"
pytestmark = [pytest.mark.maya,
              pytest.mark.skipif(not (ROOT / MESSY).is_file(), reason="fixture scenes missing: artpipe fixtures")]


def report(d, name="SM_Messy.ma"):
    return json.loads((Path(d) / (name + ".report.json")).read_text(encoding="utf-8"))


# --- the CLI filters ------------------------------------------------------------------------

def test_checks_runs_only_the_named_checks(tmp_path):
    out = commands.run(["validate", MESSY, "--class", "prop_small", "--checks", "scene.history,naming.convention",
                        "--out", str(tmp_path)], 300)
    rep = report(tmp_path)
    assert [r["id"] for r in rep["results"]] == ["naming.convention", "scene.history"]   # registry order, not ours
    assert out.exit_code == 1 and rep["summary"]["total"] == 2


def test_an_unknown_id_is_a_tool_error(tmp_path):
    out = commands.run(["validate", MESSY, "--class", "prop_small", "--checks", "scene.hstory", "--out", str(tmp_path)],
                       300)
    assert out.exit_code == 2 and "unknown check id(s) scene.hstory" in out.stderr


@pytest.mark.parametrize("args, match", [(["--fixes", "scene.history"], "--fixes needs --fix"),
                                         (["--checks", "scene.history", "--export", "x"], "partial report")])
def test_combinations_that_make_no_sense_are_refused(tmp_path, args, match):
    out = commands.run(["validate", MESSY, "--class", "prop_small", "--out", str(tmp_path)] + args, 120)
    assert out.exit_code == 2 and match in out.stderr


def test_fixes_applies_only_the_named_fix_then_revalidates_everything(tmp_path):
    out = commands.run(["validate", MESSY, "--class", "prop_small", "--fix", "--fixes", "scene.history",
                        "--fixed-dir", str(tmp_path / "fixed"), "--out", str(tmp_path)], 300)
    rep = report(tmp_path)
    assert [f["id"] for f in rep["fixes"]] == ["scene.history"] and rep["fixes"][0]["applied"]
    ids = {r["id"]: r["passed"] for r in rep["results"]}
    assert ids["scene.history"] is True and ids["naming.convention"] is False   # only history was fixed
    assert len(ids) == 20 and out.exit_code == 1


# --- over MCP -----------------------------------------------------------------------------------

RULES = ar.load()
VENV_PY = agent_cli.venv_python(RULES)
agent_ready = pytest.mark.skipif(not (VENV_PY.is_file() and agent_cli.status(RULES)["ok"]),
                                 reason="agent venv not set up: artpipe agent setup")


def calls(*steps):
    proc = subprocess.run([str(VENV_PY), str(ROOT / "tests" / "agent_client" / "calls.py"), json.dumps(list(steps))],
                          cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                          encoding="utf-8", timeout=900)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout)


@pytest.mark.agent
@agent_ready
def test_list_run_and_fix_over_mcp():
    before = hashlib.sha256((ROOT / MESSY).read_bytes()).hexdigest()
    maya, banner, check, fix = calls(["list_checks", {"domain": "maya"}], ["list_checks", {"domain": "banner"}],
                                     ["run_check", {"file": MESSY, "asset_class": "prop_small",
                                                    "check_id": "scene.history"}],
                                     ["apply_fix", {"file": MESSY, "asset_class": "prop_small",
                                                    "fix_id": "scene.history"}])
    assert len(maya["result"]["checks"]) == 20 and len(banner["result"]["checks"]) == 17
    r = check["result"]
    assert r["exit_code"] == 1 and r["result"]["id"] == "scene.history" and r["result"]["passed"] is False
    assert r["seconds"] < 5                                             # the warm worker
    f = fix["result"]
    assert f["fixes"][0]["id"] == "scene.history" and f["fixes"][0]["applied"]
    assert f["fixed_file"].startswith("out/agent/calls/") and (ROOT / f["fixed_file"]).is_file()
    assert "scene.history" not in f["failing"] and "naming.convention" in f["failing"]
    assert hashlib.sha256((ROOT / MESSY).read_bytes()).hexdigest() == before   # the source is never touched


@pytest.mark.agent
@pytest.mark.render
@agent_ready
def test_banners_check_one_check_over_mcp(tmp_path):
    out = commands.run(["banners", "render", "spotcap_grove", "--template", "tests/fixtures/banners/short_endcard.yaml",
                        "--out", str(tmp_path)], 600)
    folder = tmp_path / "spotcap_grove" / "short_endcard" / "ua_video_9x16" / "en"
    assert out.exit_code == 1 and folder.is_dir()
    (res,) = calls(["banners_check", {"folder": str(folder), "check_ids": "banner.dimensions,banner.duration"}])
    r = res["result"]
    assert [x["id"] for x in r["results"]] == ["banner.dimensions", "banner.duration"]
    assert r["status"] == "fail" and r["exit_code"] == 1               # the 1 s spot is too short


def test_an_unknown_banner_check_id_is_refused_before_any_work(tmp_path):
    from banners import check
    from banners.core import ToolError
    from banners.core import rules as br
    with pytest.raises(ToolError, match="unknown banner check id"):
        check.check(br.load(), tmp_path, only=["banner.nope"])
    assert not (tmp_path / "banner.report.json").exists()
