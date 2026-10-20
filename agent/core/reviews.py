"""The agent's judgements, recorded as data (step 5.7, backlog #48, #53).

The pipeline never judges taste: the client's model does (looking at the
images the review tools return), and its verdict is written here, so a human
can see what the agent decided and why. A verdict gates nothing by itself.

    out/agent/reviews/<id>.json             one review: target, verdict, notes
    out/agent/reviews/lineage/<root>.json   prompt changes made from one original concept

A new prompt (retry_concept) is bounded: at most `max_prompt_changes` per
original concept (conventions/agent.yaml `judgement`), every change with its
reason, so "the agent kept rewording until something passed" is visible and
capped.

Stdlib only, Python 3.9.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.core.commands import ROOT
from validate.core.locks import now

REVIEWS = ROOT / "out" / "agent" / "reviews"
VERDICTS = ("good", "reject", "unsure")
SCHEMA_VERSION = "1.0"
_RUN = re.compile(r"(out/gen/runs/concept_zimage-[0-9a-f]+)")


def _write(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    os.replace(str(tmp), str(path))


def record(target: str, verdict: str, notes: str, reviews: Path = REVIEWS) -> Dict[str, Any]:
    if verdict not in VERDICTS:
        raise ValueError("verdict must be one of %s" % ", ".join(VERDICTS))
    if not notes.strip():
        raise ValueError("say what you saw: the notes are what a human reads")
    rid = "review-%s" % uuid.uuid4().hex[:10]
    data = {"schema_version": SCHEMA_VERSION, "id": rid, "target": target, "verdict": verdict,
            "notes": notes.strip(), "at": now(), "by": "agent"}
    _write(reviews / ("%s.json" % rid), data)
    return data


def list_reviews(target: str = "", reviews: Path = REVIEWS) -> List[Dict[str, Any]]:
    out = []
    if reviews.is_dir():
        for f in sorted(reviews.glob("review-*.json")):
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not target or r["target"] == target:
                out.append(r)
    return sorted(out, key=lambda r: r["at"])


# --- prompt lineage (#53) ----------------------------------------------------------------

def _key(run: str) -> str:
    return hashlib.sha256(run.encode("utf-8")).hexdigest()[:12]


def run_of_job(job_id: str) -> Optional[str]:
    """The concept run a finished gen_concept job produced (from its output), or None."""
    from agent.core import jobs
    try:
        d = jobs.job_dir(job_id)
        text = (d / "stdout.log").read_text(encoding="utf-8", errors="replace") + \
            (d / "stderr.log").read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    found = _RUN.findall(text.replace("\\", "/"))
    return found[-1] if found else None


def root_of(run: str, reviews: Path = REVIEWS) -> str:
    """The original concept a run descends from (itself if it was never a retry)."""
    lineage = reviews / "lineage"
    if lineage.is_dir():
        for f in lineage.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for change in data["changes"]:
                if change.get("run") is None and change.get("job_id"):
                    change["run"] = run_of_job(change["job_id"])
                if change.get("run") == run:
                    return data["root"]
    return run


def lineage(root: str, reviews: Path = REVIEWS) -> Dict[str, Any]:
    path = reviews / "lineage" / ("%s.json" % _key(root))
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema_version": SCHEMA_VERSION, "root": root, "changes": []}


def add_change(root: str, subject: str, reason: str, job_id: str, max_changes: int,
               reviews: Path = REVIEWS) -> Dict[str, Any]:
    """Record one prompt change; refused past the budget."""
    data = lineage(root, reviews)
    if len(data["changes"]) >= max_changes:
        raise ValueError("%d prompt change(s) already made from %s (max %d): a human decides what next"
                         % (len(data["changes"]), root, max_changes))
    data["changes"].append({"subject": subject, "reason": reason.strip(), "job_id": job_id, "run": None,
                            "at": now()})
    _write(reviews / "lineage" / ("%s.json" % _key(root)), data)
    return data


def budget_left(root: str, max_changes: int, reviews: Path = REVIEWS) -> int:
    return max(0, max_changes - len(lineage(root, reviews)["changes"]))
