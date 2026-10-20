"""conventions/agent.yaml as typed, immutable rules (same strict reader as every other conventions file)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from validate.core.conventions import DEFAULT_DIR, ConventionError, _read_yaml, _Reader

FILE = "agent.yaml"
_VERSION = re.compile(r"^\d+\.\d+(\.\d+)?$")


@dataclass(frozen=True)
class ServerRules:
    name: str
    call_timeout_s: int


@dataclass(frozen=True)
class AgentRules:
    python: str
    venv: str
    mcp_version: str
    lock: str
    server: ServerRules
    gated: tuple = ()


def _version(r: _Reader, key: str) -> str:
    val = r.get(key, str)
    if not _VERSION.match(val):
        raise ConventionError("%s.%s: expected an exact version like 3.12 or 2.2.0, got %r" % (r.where, key, val))
    return val


def _positive(r: _Reader, key: str) -> int:
    val = r.get(key, int)
    if val <= 0:
        raise ConventionError("%s.%s: must be > 0, got %d" % (r.where, key, val))
    return val


def parse(data: dict) -> AgentRules:
    root = _Reader(data, FILE)
    root.get("version", int)
    venv = root.get("venv", str)
    if not re.match(r"^[A-Za-z0-9_.-]+$", venv):
        raise ConventionError("%s.venv: a folder name, got %r" % (FILE, venv))
    s = root.sub("server")
    server = ServerRules(s.get("name", str), _positive(s, "call_timeout_s"))
    s.done()
    gated = root.get("gated", list)
    if not gated or not all(isinstance(g, str) and re.match(r"^[a-z][a-z0-9_]*$", g) for g in gated):
        raise ConventionError("%s.gated: a list of snake_case action names" % FILE)
    rules = AgentRules(_version(root, "python"), venv, _version(root, "mcp_version"), root.get("lock", str), server,
                       tuple(gated))
    root.done()
    return rules


def load(conventions_dir: Optional[Path] = None) -> AgentRules:
    return parse(_read_yaml((conventions_dir or DEFAULT_DIR) / FILE))
