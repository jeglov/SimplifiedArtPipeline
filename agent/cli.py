"""The agent layer's own commands (run under mayapy, like every artpipe command).

    artpipe agent setup      build the server's venv: py -<python> + agent/requirements.lock (hashes, no deps)
    artpipe agent status     JSON: the venv, its Python, the installed mcp version, the lock hash
    artpipe agent serve      the MCP server over stdio (artpipe.cmd runs it in the venv, not here)

Exit codes: 0 ok / 2 tool error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from agent.core import ToolError
from agent.core import rules as agent_rules
from agent.core.commands import ROOT
from validate.core.conventions import ConventionError

MARKER = "artpipe-lock.sha256"   # written into the venv after a successful install


def tools_dir(env: Mapping[str, str] = os.environ) -> Path:
    if env.get("ARTPIPE_TOOLS"):
        return Path(env["ARTPIPE_TOOLS"])
    return Path(env.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "artpipe"


def venv_dir(rules, env: Mapping[str, str] = os.environ) -> Path:
    return tools_dir(env) / rules.venv


def venv_python(rules, env: Mapping[str, str] = os.environ) -> Path:
    return venv_dir(rules, env) / "Scripts" / "python.exe"


def lock_sha(rules) -> str:
    return hashlib.sha256((ROOT / rules.lock).read_bytes()).hexdigest()


def _run(argv, what: str) -> str:
    proc = subprocess.run([str(a) for a in argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          universal_newlines=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise ToolError("%s failed (exit %d): %s" % (what, proc.returncode, proc.stdout.strip()[-2000:]))
    return proc.stdout


def base_python(rules) -> str:
    """The py launcher's interpreter for the pinned minor version (e.g. `py -3.12`)."""
    try:
        out = _run(["py", "-%s" % rules.python, "-c", "import sys; print(sys.executable)"],
                   "py -%s (the Python launcher)" % rules.python)
    except OSError:
        raise ToolError("the Python launcher `py` is not installed: install Python %s from python.org" % rules.python)
    return out.strip().splitlines()[-1]


def installed_mcp(python: Path) -> Optional[str]:
    try:
        out = _run([python, "-c", "import importlib.metadata as m; print(m.version('mcp'))"], "mcp version")
    except (ToolError, OSError):
        return None
    return out.strip()


def status(rules, env: Mapping[str, str] = os.environ) -> Dict[str, Any]:
    py = venv_python(rules, env)
    marker = venv_dir(rules, env) / MARKER
    installed = marker.read_text(encoding="utf-8").strip() if marker.is_file() else None
    mcp = installed_mcp(py) if py.is_file() else None
    ok = py.is_file() and installed == lock_sha(rules) and mcp == rules.mcp_version
    return {"ok": ok, "venv": str(venv_dir(rules, env)), "python": rules.python, "mcp": mcp,
            "mcp_pinned": rules.mcp_version, "lock_sha256": lock_sha(rules), "installed_lock_sha256": installed}


def setup(rules, env: Mapping[str, str] = os.environ) -> str:
    if status(rules, env)["ok"]:
        return "venv up to date (%s)" % venv_dir(rules, env)
    base = base_python(rules)
    target = venv_dir(rules, env)
    _run([base, "-m", "venv", "--clear", str(target)], "creating the venv")
    py = venv_python(rules, env)
    _run([py, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--require-hashes", "--no-deps",
          "-r", str(ROOT / rules.lock)], "pip install of %s" % rules.lock)
    got = installed_mcp(py)
    if got != rules.mcp_version:
        raise ToolError("mcp %s installed, conventions/agent.yaml pins %s: the lock and the pin disagree"
                        % (got, rules.mcp_version))
    (target / MARKER).write_text(lock_sha(rules) + "\n", encoding="utf-8")
    return "venv built at %s (Python %s, mcp %s)" % (target, rules.python, got)


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="artpipe agent", description=__doc__.split("\n\n")[0])
    p.add_argument("command", choices=("setup", "status", "serve"))
    args = p.parse_args(argv)
    try:
        rules = agent_rules.load()
        if args.command == "serve":
            raise ToolError("`artpipe agent serve` runs in the agent venv: call it through artpipe.cmd")
        if args.command == "setup":
            print("artpipe agent: %s" % setup(rules))
            return 0
        state = status(rules)
        print(json.dumps(state, indent=2, sort_keys=True))
        return 0 if state["ok"] else 2
    except (ToolError, ConventionError) as exc:
        print("artpipe agent: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
