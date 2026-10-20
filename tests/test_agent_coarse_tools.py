"""Step 5.5: the coarse tools over the real MCP server (markers agent + maya; render / unity / comfy where noted).

Tools that only write under out/: validate, texture_check, banners_kit, banners_batch, gen_model, gen_ingest,
unity_verify (read-only), read_report, read_conventions.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from agent import cli as agent_cli
from agent.core import rules as ar

ROOT = Path(__file__).resolve().parents[1]
RULES = ar.load()
VENV_PY = agent_cli.venv_python(RULES)
MESSY = "out/fixtures/SM_Messy.ma"
SHORT = "tests/fixtures/banners/short_endcard.yaml"

pytestmark = [pytest.mark.agent,
              pytest.mark.skipif(not (VENV_PY.is_file() and agent_cli.status(RULES)["ok"]),
                                 reason="agent venv not set up: artpipe agent setup")]


def calls(*steps):
    proc = subprocess.run([str(VENV_PY), str(ROOT / "tests" / "agent_client" / "calls.py"), json.dumps(list(steps))],
                          cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                          encoding="utf-8", timeout=1800)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout)


def follow(job_id, timeout=1500):
    """Poll a job to the end through the server (one call per poll, like an agent would)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        (st,) = calls(["job_status", {"job_id": job_id}])
        if st["result"]["state"] in ("done", "cancelled", "lost"):
            (res,) = calls(["job_result", {"job_id": job_id}])
            return res["result"]
        time.sleep(3)
    raise AssertionError("job %s didn't finish" % job_id)


@pytest.mark.maya
@pytest.mark.skipif(not (ROOT / MESSY).is_file(), reason="fixture scenes missing: artpipe fixtures")
def test_validate_with_fix_and_export_over_mcp():
    (plain, fixed) = calls(["validate", {"file": MESSY, "asset_class": "prop_small"}],
                           ["validate", {"file": MESSY, "asset_class": "prop_small", "fix": True, "export": True}])
    p = plain["result"]
    assert p["exit_code"] == 1 and p["status"] == "fail" and any(f["id"] == "scene.history" for f in p["failing"])
    f = fixed["result"]
    assert f["exit_code"] == 0 and f["status"] == "pass" and f["failing"] == []
    assert len(f["fixes"]) >= 5 and any(x.endswith("fbx/SM_Messy.fbx") for x in f["files"])
    assert all(x.startswith("out/agent/calls/") for x in f["files"])


def test_texture_check_over_mcp():
    (r,) = calls(["texture_check", {"files": "banners/kits/spotcap_grove/logo.png"}])
    assert r["result"]["exit_code"] in (0, 1) and "logo.png" in (r["result"]["stdout"] + r["result"]["stderr"])


def test_banners_kit_over_mcp():
    (r,) = calls(["banners_kit", {"kit": "spotcap_grove", "template": "hero_endcard"}])
    res = r["result"]
    assert res["exit_code"] == 0 and res["status"] == "pass"
    assert res["report"] == "out/banners/kits/spotcap_grove.hero_endcard.kit.report.json"
    assert {x["id"] for x in res["results"]} >= {"kit.slots", "kit.licences", "kit.background"}


@pytest.mark.parametrize("tool, args", [
    ("read_report", {"path": "CLAUDE.md"}),
    ("read_report", {"path": "out/../CLAUDE.md"}),
    ("read_report", {"path": "C:/Windows/win.ini"}),
    ("read_conventions", {"name": "../CLAUDE.md"}),
])
def test_reading_outside_out_or_conventions_is_refused(tool, args):
    (r,) = calls([tool, args])
    # The reason reaches the agent (the SDK hides it unless it's raised as its ToolError).
    assert r["is_error"] and "is outside" in r["text"] and "refused" in r["text"]


def test_read_conventions_and_a_report():
    conv, kit, rep = calls(["read_conventions", {"name": "mesh_budgets.yaml"}],
                           ["banners_kit", {"kit": "spotcap_grove", "template": "hero_endcard"}],
                           ["read_report", {"path": "out/banners/kits/spotcap_grove.hero_endcard.kit.report.json"}])
    assert "tris_max" in conv["result"]["text"]
    assert rep["result"]["json"]["tool"] == "artpipe.banners.kit"


@pytest.mark.render
def test_a_banner_batch_job_over_mcp():
    (start,) = calls(["banners_batch", {"template": SHORT, "games": "spotcap_grove", "repair": True}])
    res = follow(start["result"]["job_id"])
    # The 1 s test spot fails duration + end card: no fix for those, handed over (exit 1).
    assert res["state"] == "done" and res["exit_code"] == 1
    summary = json.loads((ROOT / "out" / "banners" / "batch" / "short_endcard.batch.json").read_text(encoding="utf-8"))
    assert summary["summary"]["handed_over"] == 1 and summary["games"] == ["spotcap_grove"]


@pytest.mark.unity
def test_unity_verify_job_over_mcp():
    (start,) = calls(["unity_verify", {}])
    res = follow(start["result"]["job_id"])
    assert res["state"] == "done" and res["exit_code"] in (0, 1)


def test_the_gated_tools_are_not_exposed_directly():
    (info,) = calls(["artpipe_info", {}])
    out = subprocess.run([str(VENV_PY), str(ROOT / "tests" / "agent_client" / "stdio_client.py")], cwd=str(ROOT),
                         stdout=subprocess.PIPE, universal_newlines=True, encoding="utf-8", timeout=300)
    tools = set(json.loads(out.stdout)["tools"])
    # Writing into unity/Assets or over kit art goes through the approval queue (step 5.6).
    assert not tools & {"build", "generate", "banners_art_hero", "banners_art_background", "banners_art_logo"}
    assert {"validate", "texture_check", "gen_model", "gen_ingest", "banners_kit", "banners_batch",
            "unity_verify", "read_report", "read_conventions"} <= tools


def test_an_unknown_job_says_so():
    (r,) = calls(["job_status", {"job_id": "selftest-20000101T000000-nothere"}])
    assert r["is_error"] and "no job selftest-20000101T000000-nothere" in r["text"]


def test_tool_schemas_keep_their_parameters_through_the_error_wrapper():
    out = subprocess.run([str(VENV_PY), str(ROOT / "tests" / "agent_client" / "stdio_client.py")], cwd=str(ROOT),
                         stdout=subprocess.PIPE, universal_newlines=True, encoding="utf-8", timeout=300)
    schemas = json.loads(out.stdout)["schemas"]
    v = schemas["validate"]
    assert set(v["properties"]) == {"file", "asset_class", "fix", "export"} and set(v["required"]) == {"file", "asset_class"}
    assert schemas["job_status"]["required"] == ["job_id"]


def _passing_model_run():
    for d in sorted((ROOT / "out" / "gen" / "runs").glob("model_trellis2-*")):
        try:
            if json.loads((d / "model.report.json").read_text(encoding="utf-8"))["status"] == "pass":
                return d
        except (OSError, ValueError, KeyError):
            continue
    return None


@pytest.mark.maya
@pytest.mark.skipif(_passing_model_run() is None, reason="no passing image-to-3D run on this machine")
def test_gen_ingest_job_over_mcp():
    run = _passing_model_run()
    (start,) = calls(["gen_ingest", {"model_run": run.relative_to(ROOT).as_posix(), "asset_class": "pickup",
                                     "name": "AgentProbe"}])
    res = follow(start["result"]["job_id"])
    assert res["state"] == "done" and res["exit_code"] == 0
    assert (ROOT / "out" / "gen" / "assets" / "AgentProbe" / "SM_AgentProbe.ma").is_file()
