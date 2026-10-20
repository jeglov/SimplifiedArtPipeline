"""banner.cta_size: the CTA button and its label are big enough to read as a thumbnail. A warning.

House rules (`checks.cta_min_height_share`, `checks.cta_min_text_share`):
the CTA is the one element a UA creative can't afford to lose at feed
size. The first render had a 65 px label in a 1080 px frame and it barely
read in the contact sheet; the template now uses 84 px.
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity

ID = "banner.cta_size"
SEVERITY = Severity.WARNING
FIXABLE = False
DESCRIPTION = "Each button is at least cta_min_height_share tall, its label at least cta_min_text_share."


def run(ctx) -> CheckResult:
    rules, h = ctx.rules.checks, ctx.placement.height
    offenders, metrics = [], {}
    for layer in ctx.template.layers:
        if layer.kind != "button":
            continue
        last = ctx.state(ctx.frames()[-1], layer.id)
        height = last["box"][3] - last["box"][1]
        label = ctx.layout["page"]["font_px"][layer.id]
        metrics[layer.id] = {"height_px": round(height, 1), "label_px": label}
        if height < rules.cta_min_height_share * h:
            offenders.append("%s: %.0f px tall (min %.0f)" % (layer.id, height, rules.cta_min_height_share * h))
        if label < rules.cta_min_text_share * h:
            offenders.append("%s: label %.0f px (min %.0f)" % (layer.id, label, rules.cta_min_text_share * h))
    if not metrics:
        return CheckResult(ID, False, SEVERITY, "the template has no CTA button", FIXABLE, metrics,
                           offenders=["no button layer"])
    if offenders:
        return CheckResult(ID, False, SEVERITY, "CTA too small for a feed thumbnail: %s" % "; ".join(offenders),
                           FIXABLE, metrics, offenders=offenders)
    return CheckResult(ID, True, SEVERITY, "CTA %s" % ", ".join("%s %.0f px tall, label %.0f px" % (
        k, v["height_px"], v["label_px"]) for k, v in metrics.items()), FIXABLE, metrics)
