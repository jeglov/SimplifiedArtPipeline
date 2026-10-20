"""Client of the persistent Maya worker (validate/maya/worker.py): start it once, then talk to it.

`call("validate", argv)` runs `artpipe validate ARGV` in the warm worker and
returns {exit_code, stdout, seconds}. The worker is started on first use
(the ~21 s Maya boot, once) and outlives the MCP session that started it
(same spawn as jobs: it lives until its idle timeout). If it died or stopped
answering, it is started again and the request retried once.

Stdlib only (runs in the agent venv and under mayapy).
"""
from __future__ import annotations

import json
import socket
import time
from typing import Any, Dict, Optional, Sequence

from agent.core import ToolError
from validate.core.locks import pid_alive

from validate.maya.worker import RECORD   # stdlib-only module: importing it doesn't start Maya

START_TIMEOUT_S = 180       # Maya's cold boot is ~21 s; generous for a busy machine
PING_TIMEOUT_S = 5


def record() -> Optional[Dict[str, Any]]:
    try:
        info = json.loads(RECORD.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if pid_alive(info.get("pid")) else None


def _send(info: Dict[str, Any], op: str, argv: Sequence[str], timeout_s: float) -> Dict[str, Any]:
    with socket.create_connection(("127.0.0.1", int(info["port"])), timeout=timeout_s) as conn:
        conn.settimeout(timeout_s)
        conn.sendall((json.dumps({"token": info["token"], "op": op, "argv": list(argv)}) + "\n").encode("utf-8"))
        data = b""
        while b"\n" not in data:
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
    if not data:
        raise ConnectionError("the worker closed the connection without answering")
    reply = json.loads(data.split(b"\n", 1)[0].decode("utf-8"))
    if not reply.get("ok"):
        raise ToolError("Maya worker refused the request: %s" % reply.get("error"))
    return reply


def alive() -> Optional[Dict[str, Any]]:
    info = record()
    if info is None:
        return None
    try:
        _send(info, "ping", [], PING_TIMEOUT_S)
    except (OSError, ValueError, ConnectionError, ToolError):
        return None
    return info


def start(idle_s: Optional[float] = None) -> Dict[str, Any]:
    """Start the worker (if not answering) and wait until it answers a ping."""
    info = alive()
    if info is not None:
        return info
    from agent.core import jobs
    extra = ["--idle-s", str(idle_s)] if idle_s else []
    jobs.spawn_detached(["py", "-m", "validate.maya.worker"] + extra)
    deadline = time.time() + START_TIMEOUT_S
    while time.time() < deadline:
        info = alive()
        if info is not None:
            return info
        time.sleep(0.5)
    raise ToolError("the Maya worker didn't answer within %d s (is Maya installed and licensed?)" % START_TIMEOUT_S)


def call(op: str, argv: Sequence[str], timeout_s: float = 3600) -> Dict[str, Any]:
    """Run one request in the warm worker; restart it and retry once if it's gone."""
    for attempt in (1, 2):
        info = start()
        try:
            return _send(info, op, argv, timeout_s)
        except (OSError, ConnectionError, ValueError):
            if attempt == 2:
                raise ToolError("the Maya worker failed twice on %s %s" % (op, " ".join(argv)))
    raise AssertionError("unreachable")


def stop() -> bool:
    info = record()
    if info is None:
        return False
    try:
        with socket.create_connection(("127.0.0.1", int(info["port"])), timeout=PING_TIMEOUT_S) as conn:
            conn.sendall((json.dumps({"token": info["token"], "op": "shutdown"}) + "\n").encode("utf-8"))
            conn.recv(1024)
    except OSError:
        return False
    return True
