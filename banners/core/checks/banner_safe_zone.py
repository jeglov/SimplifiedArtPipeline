"""banner.safe_zone: text, the logo and the CTA stay out of the platform's UI, on every frame they are readable.

The margins (Meta: 14 % top, 35 % bottom, 6 % each side) are where the
feed puts its caption, profile, like/share column and CTA bar. Checked on
the DRAWN content (the text's line boxes, the logo's image rectangle), not
the layer's box: a short headline in a wide box is fine. Checked on every
frame where the layer is fully opaque, transformed (a CTA pulsing to 1.05
must stay inside at the peak); entrance frames, still fading in, are not
judged. Backgrounds and heroes may bleed.
"""
from __future__ import annotations

from banners.core.checks.banner_context import inside, outside_by, readable_layers
from banners.core.checks.context import plural
from validate.core.result import CheckResult, Severity

ID = "banner.safe_zone"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "Readable layers (text, logo, CTA) stay inside the placement's safe zone whenever fully visible."


def run(ctx) -> CheckResult:
    p, rules = ctx.placement, ctx.rules.checks
    zone = p.safe_zone.box(p.width, p.height)
    worst, offenders, checked = {}, [], 0
    for lid in readable_layers(ctx.template):
        bad_frames = []
        for n in ctx.frames():
            st = ctx.state(n, lid)
            if st["o"] < rules.visible_opacity:
                continue
            checked += 1
            if not inside(st["content"], zone, rules.safe_zone_tolerance_px):
                bad_frames.append(n)
                for edge, px in outside_by(st["content"], zone).items():
                    worst[lid + "." + edge] = max(worst.get(lid + "." + edge, 0.0), px)
        if bad_frames:
            offenders.append("%s: frames %d-%d (%s)" % (lid, bad_frames[0], bad_frames[-1], plural(len(bad_frames),
                                                                                                 "frame")))
    metrics = {"zone": list(zone), "checked": checked, "outside_px": worst}
    if offenders:
        detail = ", ".join("%s by %.0f px" % (k, v) for k, v in sorted(worst.items()))
        return CheckResult(ID, False, SEVERITY, "outside the safe zone: %s" % detail, FIXABLE, metrics,
                           offenders=offenders)
    return CheckResult(ID, True, SEVERITY, "readable layers inside x %d-%d, y %d-%d on %d visible layer-frames"
                       % (zone[0], zone[2], zone[1], zone[3], checked), FIXABLE, metrics)
