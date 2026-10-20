"""Read ComfyUI's answers: GET /history/{id} entries and POST /prompt errors.

Shapes probed on ComfyUI 0.42.1 (docs/generation.md). Pure Python 3.9.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from gen.core import ToolError

# Node output keys that hold files. "images" (SaveImage, also videos) is
# probed; "3d" (SaveGLB -> Saved3DModels) and "audio" are read from
# comfy_api/latest/_ui.py in 0.42.1, and "3d" gets a live probe in step 3.4.
# Anything else in a node's output (text, previews) is not a file.
FILE_KEYS = ("images", "3d", "audio")


@dataclass(frozen=True)
class OutputFile:
    node: str
    filename: str
    subfolder: str
    type: str  # "output" | "temp" | "input"

    def query(self) -> Dict[str, str]:
        return {"filename": self.filename, "subfolder": self.subfolder, "type": self.type}


@dataclass
class Execution:
    ok: bool
    files: List[OutputFile] = field(default_factory=list)
    executed_nodes: List[str] = field(default_factory=list)
    cached_nodes: List[str] = field(default_factory=list)
    started_ms: Optional[int] = None
    ended_ms: Optional[int] = None
    error: Optional[Dict[str, Any]] = None

    @property
    def seconds(self) -> Optional[float]:
        if self.started_ms is None or self.ended_ms is None:
            return None
        return round((self.ended_ms - self.started_ms) / 1000.0, 3)


def _node_key(nid: str) -> Tuple[int, str]:
    return (int(nid), nid) if nid.isdigit() else (1 << 30, nid)


def parse_history(entry: Mapping[str, Any], graph: Mapping[str, Any]) -> Execution:
    """One /history/{prompt_id} entry -> what ran, what was cached, which files, how long."""
    try:
        status = entry["status"]
        messages = status.get("messages") or []
        outputs = entry.get("outputs") or {}
    except (KeyError, TypeError, AttributeError):
        raise ToolError("unexpected /history entry: %r" % (entry,))

    ex = Execution(ok=status.get("status_str") == "success" and bool(status.get("completed")))
    cached = set()
    finished = None  # on an error, the nodes that completed before it (ComfyUI's "executed")
    for msg in messages:
        if not (isinstance(msg, list) and len(msg) == 2 and isinstance(msg[1], dict)):
            continue
        kind, data = msg
        ts = data.get("timestamp")
        if kind == "execution_start":
            ex.started_ms = ts
        elif kind == "execution_cached":
            cached.update(str(n) for n in data.get("nodes") or [])
        elif kind in ("execution_success", "execution_error", "execution_interrupted"):
            ex.ended_ms = ts
        if kind == "execution_error":
            ex.error = {"type": "execution_error",
                        "node": str(data.get("node_id")),
                        "node_type": data.get("node_type"),
                        "exception_type": data.get("exception_type"),
                        "message": str(data.get("exception_message", "")).strip()}
            finished = {str(n) for n in data.get("executed") or []} | {str(data.get("node_id"))}
        elif kind == "execution_interrupted":
            ex.error = {"type": "execution_interrupted", "node": str(data.get("node_id")),
                        "node_type": data.get("node_type"), "exception_type": None,
                        "message": "the run was interrupted"}
    if not ex.ok and ex.error is None:
        ex.error = {"type": status.get("status_str") or "unknown", "node": None, "node_type": None,
                    "exception_type": None, "message": "ComfyUI reported no success and no error"}

    ex.cached_nodes = sorted(cached, key=_node_key)
    # Executed = not cached; after an error only what ran up to and including
    # the failing node (probed: the nodes after it never start).
    ran = (str(n) for n in graph if str(n) not in cached and (finished is None or str(n) in finished))
    ex.executed_nodes = sorted(ran, key=_node_key)
    for nid in sorted(outputs, key=_node_key):
        for key in FILE_KEYS:
            for item in outputs[nid].get(key) or []:
                if isinstance(item, dict) and "filename" in item:
                    ex.files.append(OutputFile(str(nid), item["filename"],
                                               item.get("subfolder", ""), item.get("type", "output")))
    return ex


def prompt_error(status: int, body: Any) -> str:
    """A POST /prompt refusal (HTTP 400, validated before running) as one line.

    {"error": {"type", "message", "details"}, "node_errors": {id: {"errors": [
        {"type", "message", "details", "extra_info": {"input_name"}}], "class_type"}}}
    """
    if not isinstance(body, dict) or not isinstance(body.get("error"), dict):
        return "ComfyUI refused the graph (HTTP %d): %r" % (status, body)
    err = body["error"]
    parts = ["%s: %s" % (err.get("type"), err.get("message"))]
    if err.get("details"):
        parts.append(str(err["details"]))
    node_errors = body.get("node_errors") or {}
    for nid in sorted(node_errors, key=_node_key):
        info = node_errors[nid] or {}
        for e in info.get("errors") or []:
            name = (e.get("extra_info") or {}).get("input_name") or e.get("details") or ""
            parts.append("node %s (%s) %s: %s %s" % (nid, info.get("class_type", "?"), name,
                                                      e.get("type"), e.get("message")))
    return "ComfyUI refused the graph (HTTP %d): %s" % (status, "; ".join(parts))
