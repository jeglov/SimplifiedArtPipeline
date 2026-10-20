"""artpipe as an MCP server (stdio). Started by `artpipe agent serve` in the agent venv (Python 3.12).

The server is a thin front end: each tool runs an artpipe command
(agent/core/commands.py) and returns its exit code, its verdict and the
JSON reports it wrote. It holds no pipeline logic, so the agent sees exactly
what a human at the CLI sees, and the tested Python 3.9 code stays the only
place where rules are applied. Judgement (is this background right for the
game? which prompt next?) stays with the client: the server never calls an
LLM.

Exit codes in results keep the pipeline's contract: 0 pass / 1 the asset
failed (or was handed over) / 2 tool error.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from mcp.server.mcpserver import MCPServer

from agent.core import commands
from agent.core import rules as agent_rules

ROOT = commands.ROOT
RULES = agent_rules.load()

INSTRUCTIONS = """\
artpipe: an art pipeline for mobile games (Maya -> Unity assets, AI generation, UA video creatives).
Every tool runs one artpipe command and returns {exit_code, meaning, ...}: 0 pass, 1 the asset failed its
checks or was handed over (read the report: it says what to fix), 2 the tool broke (retry or tell a human).
Rules live in conventions/ (read them with the info tool); never guess a budget or a spec.
"""

server = MCPServer(RULES.server.name, instructions=INSTRUCTIONS, version="phase5")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, universal_newlines=True).stdout.strip()
    except OSError:
        return ""


def _json_or_text(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text.strip()


@server.tool(description="What this artpipe is: repo commit, the rules files (with sha256), the agent "
                         "layer's pins, and whether the banner renderer's pinned tools are installed. "
                         "Call it first in a session.")
def artpipe_info() -> Dict[str, Any]:
    conventions = {p.name: _sha(p) for p in sorted((ROOT / "conventions").glob("*")) if p.is_file()}
    banners = commands.run(["banners", "status"], RULES.server.call_timeout_s)
    return {
        "repo": {"head": _git_head()},
        "agent": {"python": sys.version.split()[0], "mcp_pinned": RULES.mcp_version,
                  "lock_sha256": _sha(ROOT / RULES.lock)},
        "conventions": conventions,
        "banner_tools": {"exit_code": banners.exit_code, "status": _json_or_text(banners.stdout)},
    }


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
