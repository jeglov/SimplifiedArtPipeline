"""banner.duration: inside the placement's duration range, and every frame is there.

The duration is ffprobe's container duration; the frame count is the number
of video packets. Both must agree with the template: 8 s at 30 fps is 240
frames, not 239 (a frame lost in the encode) or 241.
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity

ID = "banner.duration"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "Duration within the placement's range; frame count = template duration x fps."


def run(ctx) -> CheckResult:
    p, f = ctx.placement, ctx.facts
    dur, frames = float(f.get("duration_s") or 0.0), int(f.get("frames") or 0)
    expected = int(round(ctx.template.duration_s * p.fps))
    metrics = {"duration_s": dur, "frames": frames, "expected_frames": expected,
               "min_s": p.duration_min_s, "max_s": p.duration_max_s}
    problems = []
    if not p.duration_min_s <= dur <= p.duration_max_s:
        problems.append("%.3f s outside %s-%s s" % (dur, p.duration_min_s, p.duration_max_s))
    if frames != expected:
        problems.append("%d frames, the template needs %d" % (frames, expected))
    if problems:
        return CheckResult(ID, False, SEVERITY, "; ".join(problems), FIXABLE, metrics, offenders=problems)
    return CheckResult(ID, True, SEVERITY, "%.2f s, %d frames" % (dur, frames), FIXABLE, metrics)
