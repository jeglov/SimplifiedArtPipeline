"""A persistent mayapy worker: Maya boots once, then validates file after file (backlog #1).

    artpipe py -m validate.maya.worker [--idle-s N]       (the agent starts it; you rarely need to)

A cold `artpipe validate` pays ~21 s of Maya start-up per call; an agent that
validates, fixes and re-validates pays it every time. The worker starts Maya
once and serves requests over localhost TCP, one at a time (Maya is not
thread-safe), until it has been idle for --idle-s.

Protocol: one JSON object per line, request then response, one per
connection. stdout is NOT the channel: Maya prints its own noise there.
    -> {"token": T, "op": "ping" | "validate" | "list_checks" | "shutdown", "argv": [...]}
    <- {"ok": true, "exit_code": N, "stdout": "...", "seconds": S} | {"ok": false, "error": "..."}
The worker writes out/agent/maya_worker.json {pid, port, token, started}; a
request without that token is refused, so another local process can't drive
Maya. Each validate is the same code path as the CLI (validate.maya.cli.run),
and every file is opened into a fresh scene (session.open_scene).

Python 3.9 (mayapy).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import secrets
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
RECORD = ROOT / "out" / "agent" / "maya_worker.json"
DEFAULT_IDLE_S = 900
MAX_REQUEST = 1 << 20


def _write_record(port: int, token: str) -> None:
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    tmp = RECORD.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pid": os.getpid(), "port": port, "token": token,
                               "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n",
                   encoding="utf-8")
    os.replace(str(tmp), str(RECORD))


def _clear_record() -> None:
    try:
        if json.loads(RECORD.read_text(encoding="utf-8")).get("pid") == os.getpid():
            RECORD.unlink()
    except (OSError, ValueError):
        pass


def _read_line(conn: socket.socket) -> bytes:
    data = b""
    while b"\n" not in data:
        chunk = conn.recv(65536)
        if not chunk:
            break
        data += chunk
        if len(data) > MAX_REQUEST:
            raise ValueError("request too large")
    return data.split(b"\n", 1)[0]


def handle(request: Dict[str, Any], token: str) -> Dict[str, Any]:
    if not secrets.compare_digest(str(request.get("token", "")), token):
        return {"ok": False, "error": "bad token"}
    op = request.get("op")
    if op == "ping":
        return {"ok": True, "pid": os.getpid()}
    if op not in ("validate", "list_checks"):
        return {"ok": False, "error": "unknown op %r" % op}
    from validate.maya import cli
    argv: List[str] = [str(a) for a in request.get("argv") or []]
    out = io.StringIO()
    started = time.time()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            code = cli.run(argv) if op == "validate" else cli.list_checks()
        except SystemExit as exc:          # argparse errors exit: the worker must not
            code = exc.code if isinstance(exc.code, int) else 2
        except Exception as exc:          # noqa: BLE001 - a crash is a tool error, the worker lives on
            print("worker: %s: %s" % (type(exc).__name__, exc))
            code = 2
    return {"ok": True, "exit_code": code, "stdout": out.getvalue(), "seconds": round(time.time() - started, 3)}


def serve(idle_s: float = DEFAULT_IDLE_S) -> int:
    from validate.maya import session
    session.start()
    token = secrets.token_hex(16)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(1.0)
    _write_record(srv.getsockname()[1], token)
    last = time.time()
    try:
        while time.time() - last < idle_s:
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            with conn:
                conn.settimeout(30)
                try:
                    request = json.loads(_read_line(conn).decode("utf-8"))
                except (ValueError, OSError) as exc:
                    reply = {"ok": False, "error": "bad request: %s" % exc}
                else:
                    if request.get("op") == "shutdown" and request.get("token") == token:
                        conn.sendall(b'{"ok": true}\n')
                        break
                    conn.settimeout(None)          # a validate may take minutes
                    reply = handle(request, token)
                try:
                    conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
                except OSError:
                    pass
            last = time.time()
    finally:
        srv.close()
        _clear_record()
        session.stop()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="validate.maya.worker", description=__doc__.split("\n\n")[0])
    p.add_argument("--idle-s", type=float, default=DEFAULT_IDLE_S)
    return serve(p.parse_args(argv).idle_s)


if __name__ == "__main__":
    sys.exit(main())
