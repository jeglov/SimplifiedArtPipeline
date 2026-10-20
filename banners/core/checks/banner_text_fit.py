"""banner.text_fit: every text fits its area in at most max_lines lines.

The classic localisation failure: the English headline fits, the German one
wraps to a third line and runs into the hero, or one long word ("Spielspaß-
Garantie") is wider than the box. Measured by the layout engine (page.py
`measure`): line count = text height / line height, overflow = the text
block wider or taller than its area. Words are never broken, so an
overlong word overflows here instead of being split mid-word on screen.

Fixable: the repair shrinks the font (down to the minimum text size) and
re-renders; past that, a human shortens the string.
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity

ID = "banner.text_fit"
SEVERITY = Severity.ERROR
FIXABLE = True
DESCRIPTION = "Each text layer fits its area in at most max_lines lines."
TOLERANCE_PX = 0.5


def run(ctx) -> CheckResult:
    measure = ctx.layout["ready"]["measure"]["text"]
    offenders, metrics = [], {}
    for layer in ctx.template.layers:
        if layer.kind == "image":
            continue
        m = measure[layer.id]
        area_w, area_h = m["area"]
        metrics[layer.id] = {"lines": m["lines"], "max_lines": layer.max_lines, "width": m["width"],
                             "height": m["height"], "area": [area_w, area_h]}
        problems = []
        if m["lines"] > layer.max_lines:
            problems.append("%d lines (max %d)" % (m["lines"], layer.max_lines))
        if m["width"] > area_w + TOLERANCE_PX:
            problems.append("%d px wide in a %d px area" % (m["width"], area_w))
        if m["height"] > area_h + TOLERANCE_PX:
            problems.append("%d px tall in a %d px area" % (m["height"], area_h))
        if problems:
            offenders.append("%s: %s" % (layer.id, ", ".join(problems)))
    if offenders:
        return CheckResult(ID, False, SEVERITY, "; ".join(offenders), FIXABLE, metrics, offenders=offenders)
    return CheckResult(ID, True, SEVERITY, "every text fits (%s)" % ", ".join(
        "%s %d line%s" % (k, v["lines"], "" if v["lines"] == 1 else "s") for k, v in metrics.items()),
        FIXABLE, metrics)
