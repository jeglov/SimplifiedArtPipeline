"""The approval queue: what the agent may not do alone, and what it hands to a human.

    out/agent/queue/<item_id>.json        conventions/agent_queue.schema.json

Two kinds of item:

* approval: a GATED action the agent asked for (conventions/agent.yaml
  `gated`): anything that writes outside out/ (a build or a generation ends
  with an import into unity/Assets; kit art overwrites a game's kit; a golden
  re-record changes what the tests accept). The agent can request it and,
  once a human approved it, run it; only a human approves or rejects
  (`artpipe queue approve|reject ID`): no MCP tool can. The approval covers
  the exact command: the item stores its argv's sha256, and a run refuses an
  item whose command changed since.
* handover: a job that ended with exit 1 (an asset or banner handed over by
  its repair loop, a failed check nothing could fix), with the reason from
  the job's output. A human resolves it (`artpipe queue resolve ID`).

Stdlib only, Python 3.9.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from agent.core.commands import ROOT, check_args
from validate.core.locks import now

QUEUE = ROOT / "out" / "agent" / "queue"
SCHEMA_VERSION = "1.0"
KINDS = ("approval", "handover")
STATUSES = {"approval": ("pending", "approved", "rejected", "started"),
            "handover": ("open", "resolved")}


def _req(params: Mapping[str, Any], *names: str) -> List[str]:
    missing = [n for n in names if not str(params.get(n, "")).strip()]
    if missing:
        raise ValueError("missing parameter(s): %s" % ", ".join(missing))
    return [str(params[n]) for n in names]


def _build(p):
    scene, cls = _req(p, "scene", "asset_class")
    textures = [t for t in str(p.get("textures", "")).split(",") if t.strip()]
    return ["build", scene, "--class", cls] + sum((["--texture", t.strip()] for t in textures), []), "cpu"


def _generate(p):
    subject, cls, style, name = _req(p, "subject", "asset_class", "style", "name")
    return ["generate", subject, "--class", cls, "--style", style, "--name", name], "gpu"


def _hero(p):
    kit, run = _req(p, "kit", "concept_run")
    return ["banners", "art", "hero", kit, "--from", run], "cpu"


def _background(p):
    kit, prompt = _req(p, "kit", "prompt")
    args = ["banners", "art", "background", kit, "--prompt", prompt, "--seed", str(int(p.get("seed", 0)))]
    return args + (["--repair"] if p.get("repair", True) else []), "gpu"


def _logo(p):
    (kit,) = _req(p, "kit")
    return ["banners", "art", "logo", kit], "cpu"


def _golden(p):
    return ["py", "tests/fixtures/banners/golden_record.py"], "cpu"


# action -> (what it writes outside out/, how to turn parameters into an artpipe command + resource)
ACTIONS: Dict[str, Tuple[str, Callable[[Mapping[str, Any]], Tuple[List[str], str]]]] = {
    "build": ("imports the asset into unity/Assets (Unity project)", _build),
    "generate": ("generates an asset and imports it into unity/Assets", _generate),
    "banners_art_hero": ("overwrites the kit's hero.png (+ provenance)", _hero),
    "banners_art_background": ("overwrites the kit's background.png (+ provenance)", _background),
    "banners_art_logo": ("overwrites the kit's logo.png (+ provenance)", _logo),
    "golden_rerecord": ("re-records the golden banner the tests compare against", _golden),
}


def args_sha256(args: Sequence[str]) -> str:
    return hashlib.sha256(json.dumps(list(args), separators=(",", ":")).encode("utf-8")).hexdigest()


def _path(item_id: str, queue: Path = QUEUE) -> Path:
    if not item_id or any(c in item_id for c in "/\\:.") or item_id != item_id.strip():
        raise ValueError("not a queue item id: %r" % item_id)
    return queue / ("%s.json" % item_id)


def write(item: Mapping[str, Any], queue: Path = QUEUE) -> None:
    queue.mkdir(parents=True, exist_ok=True)
    path = _path(item["id"], queue)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(item, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    os.replace(str(tmp), str(path))


def read(item_id: str, queue: Path = QUEUE) -> Dict[str, Any]:
    path = _path(item_id, queue)          # a malformed id is refused, not reported as missing
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise KeyError("no queue item %s" % item_id)


def list_items(queue: Path = QUEUE, status: str = "", kind: str = "") -> List[Dict[str, Any]]:
    out = []
    if queue.is_dir():
        for f in sorted(queue.glob("*.json")):
            try:
                item = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (not status or item["status"] == status) and (not kind or item["kind"] == kind):
                out.append(item)
    return sorted(out, key=lambda i: i["created"])


def _new(kind: str, prefix: str) -> Dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "id": "%s-%s" % (prefix, uuid.uuid4().hex[:10]), "kind": kind,
            "created": now(), "decided": None}


def request(action: str, params: Mapping[str, Any], reason: str, queue: Path = QUEUE) -> Dict[str, Any]:
    """The agent asks for a gated action. Returns the pending item (no work is done)."""
    if action not in ACTIONS:
        raise ValueError("unknown action %r, gated actions are: %s" % (action, ", ".join(sorted(ACTIONS))))
    if not reason.strip():
        raise ValueError("say why: a human reads the reason before approving")
    effect, make = ACTIONS[action]
    args, resource = make(params)
    check_args(args)
    item = _new("approval", "approval")
    item.update(status="pending", action=action, effect=effect, params=dict(params), args=args,
                args_sha256=args_sha256(args), resource=resource, reason=reason.strip(), job_id=None)
    write(item, queue)
    return item


def decide(item_id: str, approve: bool, note: str = "", by: Optional[str] = None, queue: Path = QUEUE) -> Dict[str, Any]:
    """A human's decision (artpipe queue approve|reject). Not exposed as an MCP tool."""
    item = read(item_id, queue)
    if item["kind"] != "approval":
        raise ValueError("%s is a %s, not an approval" % (item_id, item["kind"]))
    if item["status"] != "pending":
        raise ValueError("%s is already %s" % (item_id, item["status"]))
    item.update(status="approved" if approve else "rejected",
                decided={"by": by or getpass.getuser(), "at": now(), "note": note})
    write(item, queue)
    return item


def approved_args(item_id: str, queue: Path = QUEUE) -> Tuple[Dict[str, Any], List[str]]:
    """(item, argv) of an approved item that is safe to start: approved, not started, command unchanged."""
    item = read(item_id, queue)
    if item["kind"] != "approval":
        raise ValueError("%s is a hand-over: nothing to run" % item_id)
    if item["status"] != "approved":
        raise ValueError("%s is %s: a human must approve it first (artpipe queue approve %s)"
                         % (item_id, item["status"], item_id))
    if args_sha256(item["args"]) != item["args_sha256"]:
        raise ValueError("%s: its command changed after it was requested: refused" % item_id)
    effect, make = ACTIONS[item["action"]]
    rebuilt, _ = make(item["params"])
    if rebuilt != item["args"]:
        raise ValueError("%s: its parameters no longer give the approved command: refused" % item_id)
    return item, item["args"]


def mark_started(item_id: str, job_id: str, queue: Path = QUEUE) -> Dict[str, Any]:
    item = read(item_id, queue)
    item.update(status="started", job_id=job_id, started=now())
    write(item, queue)
    return item


def handover(job: Mapping[str, Any], reason: str, queue: Path = QUEUE) -> Dict[str, Any]:
    """A job that ended with exit 1, for a human: one item per job (idempotent)."""
    existing = [i for i in list_items(queue, kind="handover") if i.get("job_id") == job["id"]]
    if existing:
        return existing[0]
    item = _new("handover", "handover")
    item.update(status="open", job_id=job["id"], tool=job["tool"], command="artpipe " + " ".join(job["args"]),
                reason=reason.strip() or "exit 1 without a message", resolution=None)
    write(item, queue)
    return item


def resolve(item_id: str, note: str, by: Optional[str] = None, queue: Path = QUEUE) -> Dict[str, Any]:
    item = read(item_id, queue)
    if item["kind"] != "handover":
        raise ValueError("%s is an approval: approve or reject it" % item_id)
    if item["status"] != "open":
        raise ValueError("%s is already %s" % (item_id, item["status"]))
    item.update(status="resolved", resolution={"by": by or getpass.getuser(), "at": now(), "note": note})
    write(item, queue)
    return item


def handover_reason(stdout: str, stderr: str) -> str:
    """The lines a human should read first: the verdict and every failing check (indented lines)."""
    lines = [ln.rstrip() for ln in (stderr + "\n" + stdout).splitlines() if ln.strip()]
    keep = [ln for ln in lines if ln.startswith("artpipe") or ln.startswith("  ")]
    return "\n".join((keep or lines)[-12:])
