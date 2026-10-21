"""How the test clients start the server: exactly as `.mcp.json` says, in Claude Code's cmd.exe setting.

Claude Code runs the command and args of `.mcp.json` from the repo root, with
`NoDefaultCurrentDirectoryInExePath=1` in its environment. With that set,
cmd.exe no longer looks in the current folder, so a bare `artpipe.cmd` is
"not recognized" (probed: the server never starts, the client sees
CONNECTION_CLOSED); `.\\artpipe.cmd` still runs. Reading `.mcp.json` here
means the live tests break when that file does. Not collected by pytest.
"""
import json
from pathlib import Path

from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parents[2]
SERVER = "artpipe"


def server_params() -> StdioServerParameters:
    entry = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"][SERVER]
    env = dict(entry.get("env") or {})
    env["NoDefaultCurrentDirectoryInExePath"] = "1"
    return StdioServerParameters(command=entry["command"], args=list(entry["args"]), cwd=str(ROOT), env=env)
