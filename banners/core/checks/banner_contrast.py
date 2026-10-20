"""banner.contrast: every text reads against what is behind it (WCAG contrast).

Measured where it matters: in the middle of the layer's fully visible time,
on the render with text and buttons hidden (so the samples are the
background, hero and logo actually behind the letters), inside the text's
drawn line boxes. The ratio is WCAG 2.x between the text colour and each
sample; the `checks.contrast_percentile` lowest share decides (robust to a
few bright specks, strict on a bright patch). Banner text is all "large"
in WCAG terms (>= 24 px bold), so the bar is `checks.contrast_min` (3:1,
WCAG AA for large text). The text shadow is NOT counted: it is margin on
top, not an excuse.

Fixable: the repair recolours the text from the kit's palette.
"""
from __future__ import annotations

from banners.core.checks.banner_context import contrast, contrast_frame, hex_rgb, percentile, sample
from validate.core.result import CheckResult, Severity

ID = "banner.contrast"
SEVERITY = Severity.ERROR
FIXABLE = True
DESCRIPTION = "Each text layer's colour has at least contrast_min against the pixels behind it."


def measure(ctx, layer):
    """(low-percentile contrast, median contrast, samples) of a text layer, or None if not measurable."""
    rules = ctx.rules.checks
    n = contrast_frame(ctx, layer.id)
    if n is None or n not in ctx.background:
        return None
    colour = hex_rgb(ctx.layout["page"]["colours"][layer.colour])
    pixels = sample(ctx.pixels(ctx.background[n]), ctx.state(n, layer.id)["content"])
    ratios = [contrast(colour, p) for p in pixels]
    if not ratios:
        return None
    return round(percentile(ratios, rules.contrast_percentile), 2), round(percentile(ratios, 0.5), 2), len(ratios), n


def run(ctx) -> CheckResult:
    rules = ctx.rules.checks
    offenders, metrics = [], {}
    for layer in ctx.template.layers:
        if layer.kind != "text":
            continue
        m = measure(ctx, layer)
        if m is None:
            offenders.append("%s: not measured (no visible key frame)" % layer.id)
            continue
        low, median, count, frame = m
        metrics[layer.id] = {"contrast_low": low, "contrast_median": median, "samples": count, "frame": frame,
                             "colour": ctx.layout["page"]["colours"][layer.colour]}
        if low < rules.contrast_min:
            offenders.append("%s: %.2f:1 on its worst %d %% (min %.1f:1)" % (
                layer.id, low, round(100 * rules.contrast_percentile), rules.contrast_min))
    metrics = {"layers": metrics, "min": rules.contrast_min, "percentile": rules.contrast_percentile}
    if offenders:
        return CheckResult(ID, False, SEVERITY, "text hard to read: %s" % "; ".join(offenders), FIXABLE, metrics,
                           offenders=offenders)
    return CheckResult(ID, True, SEVERITY, "text contrast %s" % ", ".join(
        "%s %.1f:1" % (k, v["contrast_low"]) for k, v in metrics["layers"].items()), FIXABLE, metrics)
