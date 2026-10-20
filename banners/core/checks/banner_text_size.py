"""banner.text_size: no text smaller than `checks.min_text_share` of the frame height. A warning.

A house rule, not a network spec: the feed shows the video at roughly a
quarter of the screen before the user taps, and text under ~2 % of the
frame height (38 px at 1920) is unreadable there. Measured at the size the
text is drawn after any repair (the layout's font_px) and at the smallest
animated scale while the layer is fully visible.
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity

ID = "banner.text_size"
SEVERITY = Severity.WARNING
FIXABLE = False
DESCRIPTION = "Every text is drawn at least min_text_share x frame height."


def min_visible_scale(ctx, layer_id: str) -> float:
    """Smallest on-screen size factor while fully visible: the frame's box height / the layer's box height."""
    layer = ctx.template.layer(layer_id)
    base = layer.box[3] * ctx.placement.height
    scales = [(st["box"][3] - st["box"][1]) / base for st in
              (ctx.state(n, layer_id) for n in ctx.frames()) if st["o"] >= ctx.rules.checks.visible_opacity]
    return round(min(scales), 4) if scales else 1.0


def run(ctx) -> CheckResult:
    floor = ctx.rules.checks.min_text_share * ctx.placement.height
    offenders, metrics = [], {}
    for lid, px in sorted(ctx.layout["page"]["font_px"].items()):
        drawn = px * min_visible_scale(ctx, lid)
        metrics[lid] = round(drawn, 1)
        if drawn < floor:
            offenders.append("%s: %.0f px (min %.0f)" % (lid, drawn, floor))
    metrics = {"drawn_px": metrics, "min_px": round(floor, 1)}
    if offenders:
        return CheckResult(ID, False, SEVERITY, "text too small to read in a feed: %s" % "; ".join(offenders),
                           FIXABLE, metrics, offenders=offenders)
    return CheckResult(ID, True, SEVERITY, "smallest text %.0f px (min %.0f)" % (
        min(metrics["drawn_px"].values()), floor), FIXABLE, metrics)
