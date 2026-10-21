"""Several tool calls in one session with the real server (stdio, as Claude Code connects).

    <agent venv python> tests/agent_client/calls.py '[["tool", {args}], ...]'

Prints a JSON list: per call {tool, is_error, result (structured), text}. Not collected by pytest.
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import Client

from spawn import server_params

ROOT = Path(__file__).resolve().parents[2]


async def main(calls):
    params = server_params()
    out = []
    async with Client(params, read_timeout_seconds=900) as client:
        for tool, args in calls:
            r = await client.call_tool(tool, args)
            s = r.structured_content or {}
            texts = [c.text for c in r.content if getattr(c, "type", None) == "text"]
            out.append({"tool": tool, "is_error": bool(r.is_error), "result": s.get("result", s),
                        "text": "\n".join(texts)[-3000:],
                        "types": [getattr(c, "type", None) for c in r.content],
                        "mime": [getattr(c, "mime_type", None) for c in r.content
                                 if getattr(c, "type", None) == "image"]})
    print(json.dumps(out, default=str))


if __name__ == "__main__":
    asyncio.run(main(json.loads(sys.argv[1])))
