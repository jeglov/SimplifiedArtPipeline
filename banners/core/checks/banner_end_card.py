"""banner.end_card: the logo and the CTA are fully visible from the end card's start to the last frame.

Unity requires an end card ("all video ads must also include an end card"),
and the last frame is what stays on screen when a video ends or is paused.
The hold must last at least `checks.end_card_min_s` (a house rule: under
2 s a viewer can't read the CTA before the ad closes).
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity

ID = "banner.end_card"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "Logo and every CTA fully visible on every frame from end_card_from_s to the end, for >= end_card_min_s."


def run(ctx) -> CheckResult:
    rules, t = ctx.rules.checks, ctx.template
    frames = ctx.frames()
    first = int(round(t.end_card_from_s * ctx.fps))
    hold_s = round((frames[-1] + 1 - first) / float(ctx.fps), 3)
    needed = [layer.id for layer in t.layers if layer.kind == "button" or layer.image == "logo"]
    offenders = []
    if not needed:
        offenders.append("the template has no logo or CTA layer")
    for lid in needed:
        hidden = [n for n in frames if n >= first and ctx.state(n, lid)["o"] < rules.visible_opacity]
        if hidden:
            offenders.append("%s: not fully visible on %d end-card frame(s), first %d" % (lid, len(hidden), hidden[0]))
    if hold_s < rules.end_card_min_s:
        offenders.append("end card holds %.2f s (min %.1f s)" % (hold_s, rules.end_card_min_s))
    metrics = {"from_frame": first, "hold_s": hold_s, "min_s": rules.end_card_min_s, "layers": needed}
    if offenders:
        return CheckResult(ID, False, SEVERITY, "; ".join(offenders), FIXABLE, metrics, offenders=offenders)
    return CheckResult(ID, True, SEVERITY, "%s held for the last %.2f s" % (" + ".join(needed), hold_s),
                       FIXABLE, metrics)
