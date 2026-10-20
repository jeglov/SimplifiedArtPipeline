"""Repair planning for a failed banner: deterministic, bounded, palette-only.

The policy (conventions/banners.yaml `repair`) maps each blocking check id
to an action. `plan()` decides from a banner report:

  accept     no blocking failure
  stop       some blocking failure has no fix (or `stop`): a human gets it now,
             before another render is spent
  repair     every blocking failure has a fix action: plan them

The fixes themselves are small pure functions on what the checks already
measured, so most of them need no new render:

* `fit_scale` estimates the font scale that makes a text fit from the
  layout engine's measure (line breaks are discrete, so the runner confirms
  with one-frame measure passes);
* `best_text_colour` / `best_cta` pick from the kit's palette by WCAG
  contrast against the pixel samples already taken behind the text or
  button. A repair never invents a colour.

Pure Python 3.9.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from banners.core.checks.banner_context import contrast, hex_rgb, percentile

ACCEPT, STOP, REPAIR = "accept", "stop", "repair"
SCHEMA_VERSION = "1.0"
TOOL = "artpipe.banners.repair"


@dataclass(frozen=True)
class Decision:
    decision: str                       # accept | stop | repair
    reason: str
    actions: Dict[str, str] = field(default_factory=dict)   # failing check id -> action


def blocking(report: Mapping[str, Any]) -> List[str]:
    return [r["id"] for r in report.get("results") or [] if not r["passed"] and r["severity"] == "error"]


def offender_layers(report: Mapping[str, Any], cid: str) -> List[str]:
    """Layer ids named by a check's offenders ("headline: ..." -> headline)."""
    for r in report.get("results") or []:
        if r["id"] == cid:
            return sorted({str(o).split(":")[0] for o in r.get("offenders") or []})
    return []


def plan(report: Mapping[str, Any], policy) -> Decision:
    if report.get("tool_error"):
        return Decision(STOP, "tool error: %s" % report["tool_error"])
    failed = blocking(report)
    if not failed:
        return Decision(ACCEPT, "all blocking checks pass")
    actions = {cid: policy.action_for(cid) for cid in failed}
    # with_text_fit: this failure is a consequence of text overflow (three
    # centred lines reach out of the safe zone) only if every layer it names
    # also fails text fit; then the shrink fixes both, and the re-render proves it.
    overflowing = set(offender_layers(report, "banner.text_fit")) if "banner.text_fit" in failed else set()
    for cid, a in list(actions.items()):
        if a == "with_text_fit":
            named = set(offender_layers(report, cid))
            actions[cid] = "with_text_fit" if named and named <= overflowing else "stop"
    stops = [cid for cid, a in actions.items() if a == "stop"]
    if stops:
        return Decision(STOP, "%s: no automatic fix, a human decides" % ", ".join(stops), actions)
    return Decision(REPAIR, "fixing %s" % ", ".join("%s (%s)" % (c, a) for c, a in actions.items()), actions)


def fit_scale(m: Mapping[str, Any], max_lines: int, margin: float) -> float:
    """Font scale that should make a measured text fit its area (<= 1.0).

    Height: a wrapped text's height falls roughly with the square of the
    scale (smaller lines AND fewer of them), so s = sqrt(area_h / height);
    and the same for too many lines: s <= sqrt(max_lines / lines).
    Width: an unbreakable word scales linearly: s = area_w / width.
    """
    area_w, area_h = m["area"]
    s = 1.0
    if m["height"] > area_h:
        s = min(s, math.sqrt(area_h / float(m["height"])))
    if m["lines"] > max_lines:
        s = min(s, math.sqrt(max_lines / float(m["lines"])))
    if m["width"] > area_w:
        s = min(s, area_w / float(m["width"]))
    return round(s * margin, 4) if s < 1.0 else 1.0


def _low(colour: str, samples: Sequence[Tuple[int, int, int]], share: float) -> float:
    rgb = hex_rgb(colour)
    return percentile((contrast(rgb, p) for p in samples), share)


def best_text_colour(samples: Sequence[Tuple[int, int, int]], palette: Mapping[str, str], current: str,
                     share: float, minimum: float) -> Optional[Tuple[str, float]]:
    """(palette name, low-percentile contrast) of the best colour that reaches `minimum`, or None.

    Ties break on the name, so the choice is the same on every machine.
    """
    scored = sorted(((round(_low(hexv, samples, share), 3), name) for name, hexv in palette.items()
                     if name != current), key=lambda x: (-x[0], x[1]))
    if scored and scored[0][0] >= minimum:
        return scored[0][1], scored[0][0]
    return None


def best_cta(scene: Tuple[int, int, int], palette: Mapping[str, str], fill: str, label: str,
             text_min: float, fill_min: float) -> Optional[Dict[str, Any]]:
    """A palette (fill, label) pair that reads: the fill >= fill_min against the scene, the label >= text_min on it.

    Prefers the smallest change: a new fill keeping the label (the button's
    job is to stand out), then a new label keeping the fill, then both (a
    dark fill needs a light label). Within a kind, the highest margin, then
    the names, so the choice is the same on every machine. None if no pair
    in the palette works.
    """
    def margin(f, l):
        return min(contrast(hex_rgb(palette[f]), scene) / fill_min,
                   contrast(hex_rgb(palette[l]), hex_rgb(palette[f])) / text_min)

    pairs = [(f, l) for f in sorted(palette) for l in sorted(palette) if f != l and margin(f, l) >= 1.0]
    if not pairs:
        return None

    rank = {(True, False): 1, (False, True): 2, (True, True): 3, (False, False): 0}   # (fill changed, label changed)

    def cost(pair):
        f, l = pair
        return rank[(f != fill, l != label)], -margin(f, l), f, l

    f, l = min(pairs, key=cost)
    kind = "fill" if l == label else "label" if f == fill else "both"
    return {"slot": kind, "fill": f, "label": l, "margin": round(margin(f, l), 3)}


def record(request: Mapping[str, Any], policy, attempts: Sequence[Mapping[str, Any]], decision: Decision,
           exit_code: int) -> Dict[str, Any]:
    """The repair record written next to the banner (conventions/banner_repair.schema.json)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL,
        "request": dict(request),
        "policy": {"max_renders": policy.max_renders, "max_measure_passes": policy.max_measure_passes,
                   "fit_margin": policy.fit_margin, "default": policy.default,
                   "actions": dict(sorted(policy.actions.items()))},
        "decision": decision.decision,
        "reason": decision.reason,
        "exit_code": exit_code,
        "attempts": list(attempts),
        "summary": {"renders": len(attempts),
                    "measure_passes": sum(a.get("measure_passes", 0) for a in attempts)},
    }
