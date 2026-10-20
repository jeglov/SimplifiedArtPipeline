"""conventions/generation.yaml as typed, immutable rules.

Same strict reader as validate.core.conventions: an unknown or mistyped key
fails at load time with its full path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from validate.core.conventions import DEFAULT_DIR, ConventionError, _read_yaml, _Reader

FILE = "generation.yaml"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_MODEL_ID = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class ComfyRules:
    version: str
    commit: str
    port: int
    startup_timeout_s: int
    run_timeout_s: int


@dataclass(frozen=True)
class ModelPin:
    id: str
    folder: str    # ComfyUI model folder: diffusion_models, vae, text_encoders...
    file: str
    sha256: str
    licence: str
    source: str


@dataclass(frozen=True)
class GenerationRules:
    comfyui: ComfyRules
    models: Dict[str, ModelPin]


def _positive(r: _Reader, key: str) -> int:
    val = r.get(key, int)
    if val <= 0:
        raise ConventionError("%s.%s: must be > 0, got %d" % (r.where, key, val))
    return val


def parse(data: dict) -> GenerationRules:
    root = _Reader(data, FILE)
    root.get("version", int)
    c = root.sub("comfyui")
    commit = c.get("commit", str)
    if not _COMMIT.match(commit):
        raise ConventionError("%s.comfyui.commit: expected a 40-char git hash" % FILE)
    port = _positive(c, "port")
    if port > 65535:
        raise ConventionError("%s.comfyui.port: must be <= 65535, got %d" % (FILE, port))
    comfy = ComfyRules(c.get("version", str), commit, port,
                       _positive(c, "startup_timeout_s"), _positive(c, "run_timeout_s"))
    c.done()

    models = {}
    raw_models = root.get("models", dict)
    for mid in sorted(raw_models):
        where = "%s.models.%s" % (FILE, mid)
        if not _MODEL_ID.match(str(mid)):
            raise ConventionError("%s: model id must be snake_case" % where)
        m = _Reader(raw_models[mid], where)
        pin = ModelPin(mid, m.get("folder", str), m.get("file", str), m.get("sha256", str),
                       m.get("licence", str), m.get("source", str))
        m.done()
        if not _SHA256.match(pin.sha256):
            raise ConventionError("%s.sha256: expected 64 lowercase hex chars" % where)
        if "/" in pin.file or "\\" in pin.file or "/" in pin.folder or "\\" in pin.folder:
            raise ConventionError("%s: folder and file must be plain names" % where)
        models[mid] = pin
    root.done()
    return GenerationRules(comfy, models)


def load(conventions_dir: Optional[Path] = None) -> GenerationRules:
    return parse(_read_yaml((conventions_dir or DEFAULT_DIR) / FILE))
