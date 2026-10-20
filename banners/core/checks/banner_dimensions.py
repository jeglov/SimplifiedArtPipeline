"""banner.dimensions: the video is exactly the placement's size.

Networks don't crop or pad consistently: a 1080x1918 file (an encoder
rounding, a template at the wrong size) is rejected by some and
letterboxed by others.
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity

ID = "banner.dimensions"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "The video's width and height are the placement's."


def run(ctx) -> CheckResult:
    p, f = ctx.placement, ctx.facts
    got, want = (f.get("width"), f.get("height")), (p.width, p.height)
    metrics = {"width": got[0], "height": got[1], "expected": list(want)}
    if got != want:
        return CheckResult(ID, False, SEVERITY, "video is %sx%s, %s needs %dx%d" % (got[0], got[1], p.id, want[0],
                                                                                   want[1]),
                           FIXABLE, metrics, offenders=["%sx%s" % got])
    return CheckResult(ID, True, SEVERITY, "%dx%d" % want, FIXABLE, metrics)
