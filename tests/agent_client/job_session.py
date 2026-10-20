"""Start a job through the real server, then follow it to the end, optionally from a NEW server process.

    <agent venv python> tests/agent_client/job_session.py TOOL JSON_ARGS [same|restart]

restart: the session that started the job is closed (its server exits) and a
second server process polls the job: jobs live on disk, not in the server.
Prints JSON {start, statuses (states seen), result}. Not collected by pytest.
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


def params():
    return StdioServerParameters(command=os.environ.get("COMSPEC", "cmd.exe"),
                                 args=["/d", "/c", str(ROOT / "artpipe.cmd"), "agent", "serve"], cwd=str(ROOT))


def structured(result):
    s = result.structured_content or {}
    return s.get("result", s)


async def follow(client, job_id, seen, timeout=900):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = structured(await client.call_tool("job_status", {"job_id": job_id}))
        if not seen or seen[-1]["state"] != st["state"] or st.get("comfy_progress"):
            seen.append({"state": st["state"], "queue_position": st.get("queue_position"),
                         "comfy_progress": st.get("comfy_progress")})
        if st["state"] in ("done", "cancelled", "lost"):
            return structured(await client.call_tool("job_result", {"job_id": job_id}))
        await asyncio.sleep(1.0)
    raise TimeoutError(job_id)


async def main(tool, args, mode):
    seen = []
    async with Client(params(), read_timeout_seconds=600) as client:
        start = structured(await client.call_tool(tool, args))
        if mode == "same":
            result = await follow(client, start["job_id"], seen)
    if mode == "restart":
        async with Client(params(), read_timeout_seconds=600) as client:
            result = await follow(client, start["job_id"], seen)
    print(json.dumps({"start": start, "statuses": seen, "result": result}, default=str))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], json.loads(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "same"))
