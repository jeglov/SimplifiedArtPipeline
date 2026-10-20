"""Step 5.6: the approval queue. Pure logic on a temp queue (<1 s), the human CLI, and the real
round trip over MCP: request -> refused while pending -> a human approves -> the agent runs it."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import jsonschema
import pytest

from agent import cli as agent_cli
from agent.core import commands, jobs, queue
from agent.core import rules as ar
from validate.core.conventions import DEFAULT_DIR

ROOT = Path(__file__).resolve().parents[1]
RULES = ar.load()
SCHEMA = json.loads((DEFAULT_DIR / "agent_queue.schema.json").read_text(encoding="utf-8"))


def test_the_gated_list_in_conventions_is_the_queue_s_action_list():
    assert set(RULES.gated) == set(queue.ACTIONS)


def test_request_is_pending_and_does_nothing(tmp_path):
    item = queue.request("banners_art_logo", {"kit": "spotcap_grove"}, "the logo font changed", tmp_path)
    jsonschema.validate(item, SCHEMA)
    assert item["status"] == "pending" and item["args"] == ["banners", "art", "logo", "spotcap_grove"]
    assert "overwrites" in item["effect"]
    with pytest.raises(ValueError, match="a human must approve it first"):
        queue.approved_args(item["id"], tmp_path)


@pytest.mark.parametrize("action, params, reason, match", [
    ("delete_everything", {}, "why not", "unknown action"),
    ("build", {"scene": "x.ma"}, "import it", "missing parameter"),
    ("banners_art_logo", {"kit": "spotcap_grove"}, "  ", "say why"),
    ("generate", {"subject": "a coin & del *", "asset_class": "pickup", "style": "casual_toon", "name": "C"},
     "new coin", "cmd.exe"),
])
def test_bad_requests_are_refused(tmp_path, action, params, reason, match):
    with pytest.raises(ValueError, match=match):
        queue.request(action, params, reason, tmp_path)
    assert queue.list_items(tmp_path) == []


def test_approve_then_run_once(tmp_path):
    item = queue.request("banners_art_logo", {"kit": "spotcap_grove"}, "font changed", tmp_path)
    decided = queue.decide(item["id"], True, "ok", by="lead", queue=tmp_path)
    assert decided["status"] == "approved" and decided["decided"]["by"] == "lead"
    got, args = queue.approved_args(item["id"], tmp_path)
    assert args == item["args"]
    queue.mark_started(item["id"], "job-x", tmp_path)
    with pytest.raises(ValueError, match="started"):
        queue.approved_args(item["id"], tmp_path)                    # an approval runs once
    with pytest.raises(ValueError, match="already started"):
        queue.decide(item["id"], False, queue=tmp_path)


def test_rejected_is_never_runnable(tmp_path):
    item = queue.request("golden_rerecord", {}, "renderer changed", tmp_path)
    queue.decide(item["id"], False, "not yet", queue=tmp_path)
    with pytest.raises(ValueError, match="rejected"):
        queue.approved_args(item["id"], tmp_path)


@pytest.mark.parametrize("tamper", ["args", "params", "both"])
def test_a_command_changed_after_approval_is_refused(tmp_path, tamper):
    item = queue.request("banners_art_logo", {"kit": "spotcap_grove"}, "font changed", tmp_path)
    queue.decide(item["id"], True, queue=tmp_path)
    raw = queue.read(item["id"], tmp_path)
    if tamper in ("args", "both"):
        raw["args"] = ["banners", "art", "logo", "other_game"]      # the file edited behind the queue's back
    if tamper in ("params", "both"):
        raw["params"] = {"kit": "other_game"}       # "both": consistent, so only the sha256 catches it
    queue.write(raw, tmp_path)
    with pytest.raises(ValueError, match="refused"):
        queue.approved_args(item["id"], tmp_path)


def test_handover_once_per_job_and_resolve(tmp_path):
    job = {"id": "banners-render-x", "tool": "banners_render", "args": ["banners", "render", "k"]}
    reason = queue.handover_reason("noise\nartpipe banners: FAIL out/x.json", "  error banner.duration: 1.0 s")
    a, b = queue.handover(job, reason, tmp_path), queue.handover(job, reason, tmp_path)
    assert a["id"] == b["id"] and len(queue.list_items(tmp_path, kind="handover")) == 1
    assert "banner.duration" in a["reason"] and "noise" not in a["reason"]
    jsonschema.validate(a, SCHEMA)
    done = queue.resolve(a["id"], "copy shortened", by="ua", queue=tmp_path)
    assert done["status"] == "resolved" and done["resolution"]["note"] == "copy shortened"
    with pytest.raises(ValueError, match="approval"):
        queue.resolve(queue.request("golden_rerecord", {}, "r", tmp_path)["id"], "x", queue=tmp_path)


def test_ids_are_checked(tmp_path):
    for bad in ("../x", "a/b", "", "C:x"):
        with pytest.raises(ValueError):
            queue.read(bad, tmp_path)


# --- the human CLI and the round trip over MCP ----------------------------------------------

VENV_PY = agent_cli.venv_python(RULES)
agent_ready = pytest.mark.skipif(not (VENV_PY.is_file() and agent_cli.status(RULES)["ok"]),
                                 reason="agent venv not set up: artpipe agent setup")


def calls(*steps):
    proc = subprocess.run([str(VENV_PY), str(ROOT / "tests" / "agent_client" / "calls.py"), json.dumps(list(steps))],
                          cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                          encoding="utf-8", timeout=900)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout)


def wait_job(job_id, timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = jobs.status(job_id)
        if st["state"] in jobs.FINAL:
            return st
        time.sleep(0.5)
    raise AssertionError(job_id)


@pytest.mark.agent
@pytest.mark.render
@agent_ready
def test_request_approve_run_over_mcp(tmp_path):
    kit = tmp_path / "spotcap_grove"
    shutil.copytree(str(ROOT / "banners" / "kits" / "spotcap_grove"), str(kit))
    (kit / "logo.png").unlink()
    (req, early) = calls(["queue_request", {"action": "banners_art_logo", "params": json.dumps({"kit": str(kit)}),
                                            "reason": "test: rebuild the logo of a kit copy"}],
                         ["queue_run", {"item_id": "will-be-replaced"}])
    item_id = req["result"]["id"]
    assert req["result"]["status"] == "pending" and early["is_error"]
    (still,) = calls(["queue_run", {"item_id": item_id}])
    assert still["is_error"] and "a human must approve it first" in still["text"]
    # The human approves on the command line: there is no MCP tool for it.
    out = commands.run(["queue", "approve", item_id, "--note", "fine"], 60)
    assert out.exit_code == 0 and "approved" in out.stdout
    (run,) = calls(["queue_run", {"item_id": item_id}])
    st = wait_job(run["result"]["job_id"])
    assert st["state"] == "done" and st["exit_code"] == 0 and (kit / "logo.png").is_file()
    assert queue.read(item_id)["status"] == "started" and queue.read(item_id)["job_id"] == run["result"]["job_id"]
    (again,) = calls(["queue_run", {"item_id": item_id}])
    assert again["is_error"] and "started" in again["text"]


@pytest.mark.agent
@agent_ready
def test_no_tool_can_approve():
    out = subprocess.run([str(VENV_PY), str(ROOT / "tests" / "agent_client" / "stdio_client.py")], cwd=str(ROOT),
                         stdout=subprocess.PIPE, universal_newlines=True, encoding="utf-8", timeout=300)
    tools = set(json.loads(out.stdout)["tools"])
    assert {"queue_request", "queue_list", "queue_get", "queue_run"} <= tools
    assert not any(t for t in tools if "approve" in t or "reject" in t or "resolve" in t or "decide" in t)


def test_a_job_that_ends_with_exit_1_lands_in_the_queue():
    rec = jobs.start("selftest", ["py", "-c", "import sys; print('artpipe x: HANDED OVER: text too long'); sys.exit(1)"])
    wait_job(rec["id"])
    items = [i for i in queue.list_items(kind="handover") if i["job_id"] == rec["id"]]
    assert len(items) == 1 and "text too long" in items[0]["reason"] and items[0]["status"] == "open"
    ok = jobs.start("selftest", ["py", "-c", "print('fine')"])
    wait_job(ok["id"])
    assert not [i for i in queue.list_items(kind="handover") if i["job_id"] == ok["id"]]
