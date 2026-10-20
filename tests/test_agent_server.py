"""The real MCP server over stdio, spawned the way Claude Code spawns it (marker agent, ~5 s).

The client runs in the agent venv (mayapy has no MCP SDK): tests/agent_client/stdio_client.py
launches `artpipe.cmd agent serve` with the MCP SDK's default, minimal environment, lists the
tools and calls one, and prints JSON.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent import cli as agent_cli
from agent.core import rules as ar

ROOT = Path(__file__).resolve().parents[1]
RULES = ar.load()
VENV_PY = agent_cli.venv_python(RULES)
STATE = agent_cli.status(RULES) if VENV_PY.is_file() else {"ok": False}

pytestmark = [pytest.mark.agent,
              pytest.mark.skipif(not STATE["ok"], reason="agent venv not set up: artpipe agent setup")]


def client(*args, timeout=600):
    proc = subprocess.run([str(VENV_PY), str(ROOT / "tests" / "agent_client" / "stdio_client.py")] + list(args),
                          cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                          encoding="utf-8", timeout=timeout)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout)


def result_of(report):
    structured = report["structured"] or {}
    return structured.get("result", structured)


def test_the_server_starts_and_lists_its_tools():
    r = client()
    assert r["server"] == RULES.server.name and "artpipe_info" in r["tools"]
    assert r["connect_s"] < 30


def test_artpipe_info_over_stdio_with_a_minimal_environment():
    r = client("artpipe_info")
    assert r["is_error"] is False
    info = result_of(r)
    assert set(info["conventions"]) == {p.name for p in (ROOT / "conventions").iterdir() if p.is_file()}
    assert info["agent"]["mcp_pinned"] == RULES.mcp_version and info["agent"]["python"].startswith(RULES.python)
    # The banner tools check ran through artpipe.cmd -> mayapy, which needs ProgramFiles: the MCP SDK
    # doesn't pass it, artpipe.cmd falls back to the default.
    assert info["banner_tools"]["exit_code"] in (0, 2) and isinstance(info["banner_tools"]["status"], dict)


def test_the_venv_matches_the_lock():
    assert STATE["installed_lock_sha256"] == STATE["lock_sha256"] and STATE["mcp"] == RULES.mcp_version
