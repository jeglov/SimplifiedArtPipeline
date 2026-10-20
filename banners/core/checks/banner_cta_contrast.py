"""banner.cta_contrast: the CTA label reads on its button, and the button stands out from the scene.

Two ratios per button (WCAG 2.x):
* label vs. fill, from the colours: at least `checks.cta_text_contrast_min`
  (4.5:1, WCAG AA for normal text: the label is the one text that must read
  even at thumbnail size);
* fill vs. what is behind the button (median of the text-hidden render
  inside the button's box, at its contrast frame): at least
  `checks.cta_fill_contrast_min` (3:1, WCAG's bar for UI components). A gold
  button on a golden-hour background is a CTA nobody finds.

Fixable: the repair picks another palette colour for the fill (keeping the
label's contrast) or for the label.
"""
from __future__ import annotations

from banners.core.checks.banner_context import contrast, contrast_frame, hex_rgb, sample
from validate.core.result import CheckResult, Severity

ID = "banner.cta_contrast"
SEVERITY = Severity.ERROR
FIXABLE = True
DESCRIPTION = "CTA label vs fill >= cta_text_contrast_min; fill vs the scene behind it >= cta_fill_contrast_min."


def behind(ctx, layer):
    """Median colour (per channel) behind the button at its contrast frame, or None."""
    n = contrast_frame(ctx, layer.id)
    if n is None or n not in ctx.background:
        return None
    pixels = sample(ctx.pixels(ctx.background[n]), ctx.state(n, layer.id)["box"])
    if not pixels:
        return None
    return tuple(sorted(p[c] for p in pixels)[len(pixels) // 2] for c in range(3))


def run(ctx) -> CheckResult:
    rules, colours = ctx.rules.checks, ctx.layout["page"]["colours"]
    offenders, metrics = [], {}
    buttons = [layer for layer in ctx.template.layers if layer.kind == "button"]
    for layer in buttons:
        fill, label = hex_rgb(colours[layer.fill]), hex_rgb(colours[layer.colour])
        text_ratio = round(contrast(label, fill), 2)
        scene = behind(ctx, layer)
        fill_ratio = round(contrast(fill, scene), 2) if scene else None
        metrics[layer.id] = {"label_vs_fill": text_ratio, "fill_vs_scene": fill_ratio,
                             "fill": colours[layer.fill], "label": colours[layer.colour],
                             "scene": "#%02X%02X%02X" % scene if scene else None}
        if text_ratio < rules.cta_text_contrast_min:
            offenders.append("%s: label on fill %.2f:1 (min %.1f:1)" % (layer.id, text_ratio,
                                                                       rules.cta_text_contrast_min))
        if fill_ratio is None:
            offenders.append("%s: scene behind the button not measured" % layer.id)
        elif fill_ratio < rules.cta_fill_contrast_min:
            offenders.append("%s: button on the scene %.2f:1 (min %.1f:1)" % (layer.id, fill_ratio,
                                                                             rules.cta_fill_contrast_min))
    if not buttons:
        return CheckResult(ID, False, SEVERITY, "the template has no CTA button", FIXABLE, {},
                           offenders=["no button layer"])
    if offenders:
        return CheckResult(ID, False, SEVERITY, "; ".join(offenders), FIXABLE, metrics, offenders=offenders)
    return CheckResult(ID, True, SEVERITY, "CTA %s" % ", ".join("%s label %.1f:1, button %.1f:1" % (
        k, v["label_vs_fill"], v["fill_vs_scene"]) for k, v in metrics.items()), FIXABLE, metrics)
