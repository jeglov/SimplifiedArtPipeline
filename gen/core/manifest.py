"""The provenance manifest of one generation run (conventions/gen_manifest.schema.json).

A manifest is the record of an event, not a derived report: it holds what was
asked (workflow, params, pinned models, run id), what ran it (ComfyUI build,
GPU, driver, torch) and what came out (files by sha256), plus timings. Two
runs with the same run id are the same request; their output hashes may still
differ on another GPU or driver, and the manifest is how you find out why.

Pure Python 3.9.
"""
from __future__ import annotations

import json
import posixpath
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from gen.core.history import Execution, OutputFile
from gen.core.rules import ModelPin
from gen.core.workflow import Workflow

SCHEMA_VERSION = "1.0"
TOOL = "artpipe.gen 1.0"
_SAFE_EXT = re.compile(r"^\.[a-z0-9]{1,8}$")


def output_names(files: Sequence[OutputFile], wf: Workflow) -> List[Tuple[str, str, OutputFile]]:
    """(output name, local file name, ComfyUI file) for the workflow's declared outputs only.

    One file -> "<output><ext>", several -> "<output>_1<ext>", "<output>_2<ext>"...
    in ComfyUI's order. Files from undeclared nodes (previews) are ignored.
    """
    by_node = {nid: name for name, nid in wf.outputs.items()}
    grouped = {}  # output name -> [files]
    for f in files:
        if f.node in by_node and f.type == "output":
            grouped.setdefault(by_node[f.node], []).append(f)
    out = []
    for name in sorted(grouped):
        group = grouped[name]
        for i, f in enumerate(group, 1):
            ext = posixpath.splitext(f.filename)[1].lower()
            ext = ext if _SAFE_EXT.match(ext) else ".bin"
            local = name + ext if len(group) == 1 else "%s_%d%s" % (name, i, ext)
            out.append((name, local, f))
    return out


def iso_utc(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


def git_head(repo: Path) -> Optional[str]:
    """The checked-out commit, read from .git without needing git installed."""
    git = Path(repo) / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not head.startswith("ref:"):
        return head if re.match(r"^[0-9a-f]{40}$", head) else None
    ref = head[4:].strip()
    try:
        return (git / ref).read_text(encoding="utf-8").strip()
    except OSError:
        pass
    try:  # refs packed by `git gc`
        for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                return parts[0]
    except OSError:
        pass
    return None


# Server flags whose value is a path on this machine: recorded as "<path>" so
# the manifest says how ComfyUI ran without leaking the machine's layout.
PATH_FLAGS = ("--extra-model-paths-config", "--input-directory", "--output-directory",
              "--temp-directory", "--user-directory", "--base-directory")


def server_argv(argv: Sequence[str]) -> List[str]:
    out, hide = [], False
    for a in list(argv)[1:]:  # [0] is main.py
        out.append("<path>" if hide else a)
        hide = a in PATH_FLAGS
    return out


def gpu_name(device: Optional[str]) -> Optional[str]:
    """"cuda:0 NVIDIA GeForce RTX 3070 Laptop GPU : cudaMallocAsync" -> the GPU name."""
    if not device:
        return None
    name = device.split(" : ")[0]
    if name.startswith("cuda:") and " " in name:
        name = name.split(" ", 1)[1]
    return name.strip() or None


def environment(system_stats: Mapping[str, Any], commit: Optional[str], dirty: Optional[bool],
                driver: Optional[str]) -> Dict[str, Any]:
    """What ran the graph, from GET /system_stats plus what it can't tell us."""
    system = system_stats.get("system") or {}
    devices = system_stats.get("devices") or [{}]
    dev = devices[0] if devices else {}
    vram = dev.get("vram_total")
    return {
        "comfyui_version": system.get("comfyui_version"),
        "comfyui_commit": commit,
        "comfyui_local_changes": dirty,
        "python": str(system.get("python_version", "")).split(" ")[0] or None,
        "torch": system.get("pytorch_version"),
        "gpu": gpu_name(dev.get("name")),
        "vram_mb": int(vram // (1024 * 1024)) if isinstance(vram, (int, float)) else None,
        "driver": driver,
        "argv": server_argv(system.get("argv") or []),
    }


def build(*, wf: Workflow, workflow_path: str, params: Mapping[str, Any], rid: str,
          prompt_id: Optional[str], models: Sequence[Tuple[ModelPin, str]],
          env: Mapping[str, Any], execution: Optional[Execution],
          outputs: Sequence[Mapping[str, Any]], error: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    ok = error is None
    ex = execution
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL,
        "run_id": rid,
        "status": "ok" if ok else "error",
        "exit_code": 0 if ok else 2,
        "error": dict(error) if error else None,
        "workflow": {"id": wf.id, "path": workflow_path, "sha256": wf.sha256},
        "params": dict(sorted(params.items())),
        "models": [{"id": p.id, "folder": p.folder, "file": p.file, "sha256": sha,
                    "licence": p.licence, "source": p.source} for p, sha in models],
        "outputs": list(outputs),
        "environment": dict(env),
        "execution": {
            "prompt_id": prompt_id,
            "started_at": iso_utc(ex.started_ms) if ex else None,
            "seconds": ex.seconds if ex else None,
            "executed_nodes": list(ex.executed_nodes) if ex else [],
            "cached_nodes": list(ex.cached_nodes) if ex else [],
        },
    }


def to_json(manifest: Mapping[str, Any]) -> str:
    return json.dumps(manifest, indent=2, sort_keys=False, ensure_ascii=False) + "\n"
