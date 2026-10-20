"""banner.frame_rate: constant, and the placement's rate.

Meta asks for a "fixed frame rate". A variable-rate file (what screen
capture produces) plays with judder after the network re-encodes it. Proven
from every packet's timestamp, not from the header: ffprobe prints 6
decimals, so 1/30 s shows as 0.033333 or 0.033334, nothing else.
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity

ID = "banner.frame_rate"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "Every frame interval is 1/fps of the placement (constant frame rate)."
TOLERANCE_S = 2e-6


def run(ctx) -> CheckResult:
    p, f = ctx.placement, ctx.facts
    intervals = f.get("frame_intervals_s") or []
    want = 1.0 / p.fps
    off = [i for i in intervals if abs(i - want) > TOLERANCE_S]
    metrics = {"fps": p.fps, "r_frame_rate": f.get("r_frame_rate"), "avg_frame_rate": f.get("avg_frame_rate"),
               "intervals_s": intervals}
    if not intervals:
        return CheckResult(ID, False, SEVERITY, "no frame timestamps", FIXABLE, metrics, offenders=["timestamps"])
    if off or f.get("r_frame_rate") != p.fps:
        return CheckResult(ID, False, SEVERITY, "frame intervals %s s, header %s fps: expected a constant %d fps"
                           % (intervals, f.get("r_frame_rate"), p.fps), FIXABLE, metrics,
                           offenders=[str(v) for v in off] or ["r_frame_rate %s" % f.get("r_frame_rate")])
    return CheckResult(ID, True, SEVERITY, "constant %d fps" % p.fps, FIXABLE, metrics)
