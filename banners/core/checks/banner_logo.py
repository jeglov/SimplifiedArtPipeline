"""banner.logo: the logo is drawn at its own aspect ratio, whole, and inside the frame.

A stretched or cropped logo is the first thing a brand team rejects.
Measured from the layout engine: the drawn image size vs. the file's
natural size (aspect within `checks.logo_aspect_tolerance`), and the drawn
rectangle inside the frame on every fully visible frame. Rotation is not
allowed on a logo layer (it would make the bounding box lie about the
aspect).
"""
from __future__ import annotations

from banners.core.checks.banner_context import inside
from validate.core.result import CheckResult, Severity

ID = "banner.logo"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "Logo layers keep their aspect ratio and lie wholly inside the frame when visible."


def run(ctx) -> CheckResult:
    rules = ctx.rules.checks
    images = ctx.layout["ready"]["measure"]["images"]
    frame = (0, 0, ctx.placement.width, ctx.placement.height)
    logos = [layer for layer in ctx.template.layers if layer.image == "logo"]
    if not logos:
        return CheckResult(ID, False, SEVERITY, "the template has no logo layer", FIXABLE, {},
                           offenders=["no logo layer"])
    offenders, metrics = [], {}
    for layer in logos:
        m = images[layer.id]
        natural = m["natural"][0] / float(m["natural"][1])
        drawn = m["drawn"][0] / float(m["drawn"][1])
        distortion = abs(drawn / natural - 1.0)
        metrics[layer.id] = {"natural": m["natural"], "drawn": m["drawn"], "distortion": round(distortion, 4)}
        if distortion > rules.logo_aspect_tolerance:
            offenders.append("%s: drawn %dx%d, the file is %dx%d (%.1f %% stretched)" % (
                layer.id, m["drawn"][0], m["drawn"][1], m["natural"][0], m["natural"][1], 100 * distortion))
        if "rotate" in layer.tracks:
            offenders.append("%s: a logo layer must not rotate" % layer.id)
        cropped = [n for n in ctx.frames() if ctx.state(n, layer.id)["o"] >= rules.visible_opacity
                   and not inside(ctx.state(n, layer.id)["content"], frame, rules.safe_zone_tolerance_px)]
        if cropped:
            offenders.append("%s: cropped by the frame edge on %d frame(s)" % (layer.id, len(cropped)))
    if offenders:
        return CheckResult(ID, False, SEVERITY, "; ".join(offenders), FIXABLE, metrics, offenders=offenders)
    return CheckResult(ID, True, SEVERITY, "logo at its own aspect, whole", FIXABLE, metrics)
