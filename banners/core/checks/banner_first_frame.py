"""banner.first_frame: frame 0 shows something: not blank, and a hero or text already on screen.

Networks and feeds show the first frame as the poster before autoplay (and
when autoplay is off): a creative that fades in from black is a black
thumbnail. Measured on the frame DECODED from banner.mp4: the luminance
spread (standard deviation, 0-255) over a sample grid must reach
`checks.first_frame_min_std`, and at least one layer other than the
background must be fully visible at frame 0 in the layout record.
"""
from __future__ import annotations

import math

from banners.core.checks.banner_context import luminance, sample
from validate.core.result import CheckResult, Severity

ID = "banner.first_frame"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "The decoded first frame is not blank and already shows a hero or text layer."
GRID_STEP = 8


def run(ctx) -> CheckResult:
    rules = ctx.rules.checks
    if 0 not in ctx.decoded:
        return CheckResult(ID, False, SEVERITY, "frame 0 was not decoded", FIXABLE, {}, offenders=["frame 0"])
    px = ctx.pixels(ctx.decoded[0])
    lum = [255.0 * luminance(c) for c in sample(px, (0, 0, px.width, px.height), GRID_STEP)]
    mean = sum(lum) / len(lum)
    std = math.sqrt(sum((v - mean) ** 2 for v in lum) / len(lum))
    first_layer = ctx.template.layers[0].id
    shown = [layer.id for layer in ctx.template.layers[1:]
             if ctx.state(0, layer.id)["o"] >= rules.visible_opacity]
    metrics = {"luminance_std": round(std, 2), "luminance_mean": round(mean, 2), "min_std": rules.first_frame_min_std,
               "visible_layers": shown, "background_layer": first_layer}
    offenders = []
    if std < rules.first_frame_min_std:
        offenders.append("frame 0 is nearly flat (luminance spread %.1f, min %.1f)" % (std, rules.first_frame_min_std))
    if not shown:
        offenders.append("only the background is on screen at frame 0")
    if offenders:
        return CheckResult(ID, False, SEVERITY, "; ".join(offenders), FIXABLE, metrics, offenders=offenders)
    return CheckResult(ID, True, SEVERITY, "frame 0 shows %s (spread %.0f)" % (", ".join(shown), std), FIXABLE,
                       metrics)
