"""Cross-process locks as files holding the holder's pid; a dead holder's lock is stale and taken over.

Used by the ComfyUI server lifecycle (gen/comfy.py, backlog #45) and the
agent's job queue (agent/core/jobs.py). Atomic by O_EXCL create: of two
processes racing for a lock, exactly one gets it. Stdlib only, Python 3.9.
"""
from __future__ import annotations

import ctypes
import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

EMPTY_STALE_S = 10.0   # an empty lock file older than this lost its writer


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def pid_alive(pid: Optional[int]) -> bool:
    """True if a process with this pid is running (Windows: OpenProcess + GetExitCodeProcess)."""
    if not pid or int(pid) <= 0:
        return False
    if sys.platform != "win32":
        try:
            os.kill(int(pid), 0)
        except OSError:
            return False
        return True
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259                              # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def holder(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def try_lock(path: Path, owner: str, pid: Optional[int] = None) -> bool:
    """Take the lock if it is free or its holder is dead."""
    pid = pid or os.getpid()
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            h = holder(path)
            if h is None:
                # Created but not filled: being written right now, or its writer died in between.
                try:
                    age = time.time() - path.stat().st_mtime
                except OSError:
                    continue
                if age < EMPTY_STALE_S:
                    return False
            elif pid_alive(h.get("pid")):
                return False
            try:
                path.unlink()       # stale: its holder is gone
            except OSError:
                return False
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"owner": owner, "pid": pid, "since": now()}))
        return True
    return False


def acquire(path: Path, owner: str, timeout_s: float, poll_s: float = 0.2) -> bool:
    deadline = time.time() + timeout_s
    while True:
        if try_lock(path, owner):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(poll_s)


def release(path: Path, owner: str) -> None:
    h = holder(path)
    if h and h.get("owner") == owner:
        try:
            path.unlink()
        except OSError:
            pass
