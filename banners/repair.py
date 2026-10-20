"""Render, check, repair, re-render: a banner through the repair policy (conventions/banners.yaml `repair`).

    render -> check -> plan (banners/core/repair.py)
       accept: done          stop: hand over          repair: fix, render again
    at most `max_renders` full renders; `repair.json` next to the banner records every attempt
    (conventions/banner_repair.schema.json).

Fixes, cheapest first:
* shrink_text: one-frame measure passes (~3 s each, not a 2 min render)
  until the layout engine says every failing text fits, never below
  checks.min_text_share; at the floor and still failing, hand over.
* recolour_text: the palette colour with the best low-percentile contrast
  against the pixels already sampled behind every layer using that colour
  slot; the shadow slot follows (the palette colour that contrasts most with
  the new text colour), so dark text doesn't get a dark halo.
* recolour_cta: a palette fill that reads on the scene and under the label,
  else a palette label for the current fill.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from banners import build, check as banner_check, render
from banners.core import page as page_mod
from banners.core import repair as rp
from banners.core.checks.banner_context import contrast, contrast_frame, hex_rgb, percentile, sample
from banners.core.checks.banner_contrast import measure as measure_contrast
from banners.core.checks.banner_cta_contrast import behind
from banners.core.kit import Font, Kit
from banners.core.rules import BannerRules
from banners.core.template import Template

RECORD = "repair.json"


def _fits(m: Mapping[str, Any], max_lines: int) -> bool:
    return m["lines"] <= max_lines and m["width"] <= m["area"][0] + 0.5 and m["height"] <= m["area"][1] + 0.5


def measure(rules: BannerRules, template: Template, kit: Kit, fonts: Mapping[str, Font], placement: str,
            locale: str, overrides: page_mod.Overrides) -> Dict[str, Any]:
    """The layout engine's text measure for these overrides: one frame, no encode (~3 s)."""
    p = rules.placement(placement)
    pg = page_mod.build(template, kit, fonts, p, locale, overrides)
    with tempfile.TemporaryDirectory() as tmp:
        index = Path(tmp) / "index.html"
        index.write_text(pg.html, encoding="utf-8")
        record = render.frames(rules, files=dict(pg.files, **{"index.html": index}), page="index.html",
                               width=p.width, height=p.height, frame_list=[0], out_dir=Path(tmp) / "f",
                               locale=locale)
    return record["ready"]["measure"]["text"]


def shrink_text(rules: BannerRules, template: Template, kit: Kit, fonts, placement: str, locale: str,
                overrides: page_mod.Overrides, failing: List[str]) -> Tuple[Optional[page_mod.Overrides], int, str]:
    """(new overrides or None if a text can't fit above the size floor, measure passes, note)."""
    p = rules.placement(placement)
    scales = dict(overrides.font_scale)
    floor_px = rules.checks.min_text_share * p.height
    passes, m = 0, None
    for _ in range(rules.repair.max_measure_passes):
        m = measure(rules, template, kit, fonts, placement, locale, page_mod.Overrides(scales, overrides.colours))
        passes += 1
        still = [lid for lid in failing if not _fits(m[lid], template.layer(lid).max_lines)]
        if not still:
            notes = ", ".join("%s x%.3f" % (lid, scales.get(lid, 1.0)) for lid in failing)
            return page_mod.Overrides(scales, overrides.colours), passes, "text fits at %s" % notes
        for lid in still:
            layer = template.layer(lid)
            floor = floor_px / (layer.size * p.height)
            current = scales.get(lid, 1.0)
            if current <= floor + 1e-9:
                return None, passes, ("%s still doesn't fit at the minimum text size (%.0f px): shorten the "
                                      "string" % (lid, floor_px))
            step = rp.fit_scale(m[lid], layer.max_lines, rules.repair.fit_margin)
            if step >= 1.0:          # measured as fitting by size but not by lines: nudge
                step = rules.repair.fit_margin
            scales[lid] = round(max(floor, current * step), 4)
    return None, passes, "no fit after %d measure passes" % passes


def recolour_text(ctx, overrides: page_mod.Overrides, kit: Kit) -> Tuple[Optional[page_mod.Overrides], str]:
    rules = ctx.rules.checks
    colours = dict(overrides.colours)
    failing_slots = {}
    for layer in ctx.template.layers:
        if layer.kind != "text":
            continue
        m = measure_contrast(ctx, layer)
        if m is not None and m[0] < rules.contrast_min:
            failing_slots.setdefault(layer.colour, []).append(layer)
    notes = []
    for slot, _ in sorted(failing_slots.items()):
        users = [layer for layer in ctx.template.layers if layer.kind == "text" and layer.colour == slot]
        per_layer = []
        for layer in users:
            n = contrast_frame(ctx, layer.id)
            per_layer.append(sample(ctx.pixels(ctx.background[n]), ctx.state(n, layer.id)["content"]))
        current = colours.get(slot, kit.colours[slot])
        scored = []
        for name, value in sorted(kit.palette.items()):
            if name == current:
                continue
            low = min(percentile((contrast(hex_rgb(value), px) for px in s), rules.contrast_percentile)
                      for s in per_layer if s)
            scored.append((round(low, 3), name))
        scored.sort(key=lambda x: (-x[0], x[1]))
        if not scored or scored[0][0] < rules.contrast_min:
            return None, "no palette colour reaches %.1f:1 behind %s: change the background or the palette" % (
                rules.contrast_min, ", ".join(layer.id for layer in users))
        colours[slot] = scored[0][1]
        notes.append("%s -> %s (%.1f:1)" % (slot, scored[0][1], scored[0][0]))
        # The shadow follows: the palette colour farthest from the new text colour.
        shadows = {layer.shadow for layer in users if layer.shadow}
        for sh in sorted(shadows):
            new = hex_rgb(kit.palette[colours[slot]])
            best = sorted(kit.palette, key=lambda nm: (-contrast(hex_rgb(kit.palette[nm]), new), nm))[0]
            colours[sh] = best
            notes.append("%s -> %s" % (sh, best))
    return page_mod.Overrides(overrides.font_scale, colours), "; ".join(notes)


def recolour_cta(ctx, overrides: page_mod.Overrides, kit: Kit) -> Tuple[Optional[page_mod.Overrides], str]:
    rules = ctx.rules.checks
    colours = dict(overrides.colours)
    notes = []
    for layer in ctx.template.layers:
        if layer.kind != "button":
            continue
        fill = colours.get(layer.fill, kit.colours[layer.fill])
        label = colours.get(layer.colour, kit.colours[layer.colour])
        scene = behind(ctx, layer)
        if scene is None:
            return None, "%s: the scene behind the button was not measured" % layer.id
        fine = contrast(hex_rgb(kit.palette[fill]), scene) >= rules.cta_fill_contrast_min and \
            contrast(hex_rgb(kit.palette[label]), hex_rgb(kit.palette[fill])) >= rules.cta_text_contrast_min
        if fine:
            continue
        pick = rp.best_cta(scene, kit.palette, fill, label, rules.cta_text_contrast_min, rules.cta_fill_contrast_min)
        if pick is None:
            return None, "%s: no palette fill or label colour makes the CTA read: change the palette" % layer.id
        colours[layer.fill], colours[layer.colour] = pick["fill"], pick["label"]
        notes.append("%s fill %s, label %s (%.2fx the bar)" % (layer.id, pick["fill"], pick["label"], pick["margin"]))
    return page_mod.Overrides(overrides.font_scale, colours), "; ".join(notes)


def _overrides_dict(o: page_mod.Overrides) -> Dict[str, Any]:
    return {"font_scale": dict(sorted(o.font_scale.items())), "colours": dict(sorted(o.colours.items()))}


def run(rules: BannerRules, template: Template, kit: Kit, fonts: Mapping[str, Font], placement: str, locale: str,
        out: Path = build.OUT, folder: Optional[Path] = None) -> Tuple[int, Dict[str, Any], Path]:
    """(exit code, repair record, banner folder). 0 accepted / 1 handed over / 2 tool error."""
    overrides = page_mod.Overrides()
    attempts: List[Dict[str, Any]] = []
    decision = rp.Decision(rp.STOP, "no render")
    exit_code = 2
    target = folder or build.banner_dir(out, kit, template, placement, locale)
    for n in range(1, rules.repair.max_renders + 1):
        r = build.render_banner(rules, template, kit, fonts, placement, locale, out, overrides, target)
        report, path = banner_check.check(rules, r.folder, template)
        data = report.to_dict()
        decision = rp.plan(data, rules.repair)
        attempt = {"render": n, "overrides": _overrides_dict(overrides), "failed": rp.blocking(data),
                   "warnings": [x["id"] for x in data["results"] if not x["passed"] and x["severity"] == "warning"],
                   "decision": decision.decision, "reason": decision.reason, "measure_passes": 0, "changes": [],
                   "mp4_sha256": data["source"]["sha256"]}
        attempts.append(attempt)
        if decision.decision == rp.ACCEPT:
            exit_code = 0
            break
        if decision.decision == rp.STOP:
            exit_code = 2 if data.get("tool_error") else 1
            break
        if n == rules.repair.max_renders:
            decision = rp.Decision(rp.STOP, "still failing after %d renders: %s" % (n, ", ".join(attempt["failed"])))
            attempt["decision"], attempt["reason"] = decision.decision, decision.reason
            exit_code = 1
            break
        ctx = banner_check.context(rules, r.folder, template)
        new = overrides
        for cid, action in sorted(decision.actions.items()):
            note = ""
            if action == "shrink_text":
                failing = [k for k, v in data_metrics(data, cid).items()
                           if isinstance(v, dict) and not _fits_metric(v)]
                new, passes, note = shrink_text(rules, template, kit, fonts, placement, locale, new, failing)
                attempt["measure_passes"] += passes
            elif action == "recolour_text":
                new, note = recolour_text(ctx, new, kit)
            elif action == "recolour_cta":
                new, note = recolour_cta(ctx, new, kit)
            elif action == "with_text_fit":
                note = "expected to clear with the text fit (%s)" % ", ".join(rp.offender_layers(data, cid))
            attempt["changes"].append({"check": cid, "action": action, "note": note})
            if new is None:
                break
        if new is None or _overrides_dict(new) == _overrides_dict(overrides):
            why = attempt["changes"][-1]["note"] if attempt["changes"] else "the fixes changed nothing"
            decision = rp.Decision(rp.STOP, why or "the fixes changed nothing", decision.actions)
            attempt["decision"], attempt["reason"] = decision.decision, decision.reason
            exit_code = 1
            break
        overrides = new
    rec = rp.record({"kit": kit.id, "template": template.id, "placement": placement, "locale": locale},
                    rules.repair, attempts, decision, exit_code)
    rec["report"] = banner_check.REPORT     # next to this record: no machine path, wherever the folder lives
    with (target / RECORD).open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
    return exit_code, rec, target


def data_metrics(report: Mapping[str, Any], cid: str) -> Dict[str, Any]:
    for r in report["results"]:
        if r["id"] == cid:
            return r["metrics"]
    return {}


def _fits_metric(v: Mapping[str, Any]) -> bool:
    return v["lines"] <= v["max_lines"] and v["width"] <= v["area"][0] + 0.5 and v["height"] <= v["area"][1] + 0.5
