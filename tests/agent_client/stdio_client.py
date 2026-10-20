"""Talk to the real server the way Claude Code does: spawn `artpipe.cmd agent serve`, speak MCP over stdio.

    <agent venv python> tests/agent_client/stdio_client.py [TOOL [JSON_ARGS]]

Prints one JSON object: the server info, the tool list and (if TOOL is given)
the tool's result. Run by tests/test_agent_server.py under the agent venv
(mayapy has no MCP SDK). Not collected by pytest.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parents[2]


def _content(result):
    out = []
    for c in result.content:
        if getattr(c, "type", None) == "text":
            try:
                out.append(json.loads(c.text))
            except ValueError:
                out.append(c.text)
        else:
            out.append({"type": getattr(c, "type", None)})
    return out


async def main(tool, args):
    params = StdioServerParameters(command=os.environ.get("COMSPEC", "cmd.exe"),
                                   args=["/d", "/c", str(ROOT / "artpipe.cmd"), "agent", "serve"], cwd=str(ROOT))
    started = time.time()
    async with Client(params, read_timeout_seconds=600) as client:
        connected = time.time() - started
        tools = await client.list_tools()
        report = {"server": client.server_info.name if client.server_info else None,
                  "connect_s": round(connected, 2),
                  "tools": sorted(t.name for t in tools.tools),
                  "schemas": {t.name: t.input_schema for t in tools.tools}}
        if tool:
            t0 = time.time()
            result = await client.call_tool(tool, args)
            report["call_s"] = round(time.time() - t0, 2)
            report["is_error"] = bool(result.is_error)
            report["structured"] = result.structured_content
            report["content"] = _content(result)
    print(json.dumps(report, default=str))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None,
                     json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}))
