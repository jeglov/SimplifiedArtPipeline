"""Batch logic: the input hash a banner is reused by, and the batch summary. Pure Python 3.9.

A banner is reused only when EVERY input that can change it is unchanged:
the template file, the kit (kit.yaml, every image and font by sha256), the
placement and every rule it is checked or repaired by, the pinned tools
(versions and exe hashes), and the banner code itself (every file of the
banners package and the renderer script). Changing a check threshold
therefore re-checks and re-renders: a cached verdict under an old rule is
a wrong verdict.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any, Dict, List, Mapping, Sequence, Tuple

SCHEMA_VERSION = "1.0"
TOOL = "artpipe.banners.batch"


def canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def rules_record(rules, placement: str) -> Dict[str, Any]:
    """Everything in banners.yaml that affects one placement's banner."""
    asdict = dataclasses.asdict
    return {"placement": asdict(rules.placement(placement)), "encode": asdict(rules.encode),
            "checks": asdict(rules.checks), "repair": asdict(rules.repair), "licences": asdict(rules.licences),
            "tools": {"ffmpeg": asdict(rules.ffmpeg), "chromium": asdict(rules.chromium),
                      "playwright": asdict(rules.playwright)}}


def input_hash(parts: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical(parts).encode("utf-8")).hexdigest()


def histogram(pixels: Sequence[Tuple[int, int, int]], bins: int = 4) -> List[float]:
    """Normalised RGB histogram with bins^3 cells (coarse: palette and value, not detail)."""
    counts = [0] * (bins ** 3)
    step = 256 // bins
    for r, g, b in pixels:
        counts[(r // step) * bins * bins + (g // step) * bins + (b // step)] += 1
    total = float(len(pixels)) or 1.0
    return [c / total for c in counts]


def distance(a: Sequence[float], b: Sequence[float]) -> float:
    """Half the L1 distance: 0 for identical histograms, 1 for no colour in common."""
    return round(sum(abs(x - y) for x, y in zip(a, b)) / 2.0, 4)


def distinct_pairs(hists: Mapping[str, Sequence[float]], minimum: float) -> List[Dict[str, Any]]:
    """Game pairs whose end cards are closer than `minimum` (same placement and locale)."""
    names = sorted(hists)
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            d = distance(hists[a], hists[b])
            if d < minimum:
                out.append({"a": a, "b": b, "distance": d})
    return out


def summary(banners: Sequence[Mapping[str, Any]], kits: Sequence[Mapping[str, Any]],
            distinct: Mapping[str, Any], seconds: float) -> Dict[str, Any]:
    """Counts, failures by check, time: what a UA lead or an agent reads first."""
    by_check: Dict[str, int] = {}
    for b in banners:
        for cid in b.get("failed", []):
            by_check[cid] = by_check.get(cid, 0) + 1
    statuses = [b["status"] for b in banners]
    return {
        "banners": len(banners),
        "passed": statuses.count("pass"),
        "handed_over": statuses.count("handed_over"),
        "errors": statuses.count("error"),
        "skipped": statuses.count("skipped"),
        "reused": sum(1 for b in banners if b.get("reused")),
        "repaired": sum(1 for b in banners if b.get("renders", 0) > 1),
        "kits_failed": sum(1 for k in kits if k["status"] != "pass"),
        "failures_by_check": dict(sorted(by_check.items())),
        "similar_games": len(distinct.get("pairs", [])),
        "seconds": round(seconds, 1),
        "render_seconds": round(sum(b.get("seconds", 0.0) for b in banners if not b.get("reused")), 1),
    }


def exit_code(banners: Sequence[Mapping[str, Any]], kits: Sequence[Mapping[str, Any]]) -> int:
    """2 if any tool error, else 1 if any banner or kit failed, else 0."""
    if any(b["status"] == "error" for b in banners) or any(k["status"] == "error" for k in kits):
        return 2
    if any(b["status"] != "pass" for b in banners) or any(k["status"] != "pass" for k in kits):
        return 1
    return 0
