"""banner.font_fallback: every glyph was drawn by the kit's fonts.

Chromium falls back per glyph, silently: Fredoka has no Cyrillic, so a
Russian headline comes out half Fredoka, half Times New Roman, with no
error anywhere (probed). The layout record holds Chromium's own answer
(CDP CSS.getPlatformFontsForNode): any font that isn't one of the page's
loaded faces is a fallback.
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity

ID = "banner.font_fallback"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "No text layer has a glyph drawn by a system fallback font."


def run(ctx) -> CheckResult:
    fonts = ctx.layout.get("fonts") or {}
    offenders, metrics = [], {}
    for layer in ctx.template.layers:
        if layer.kind == "image":
            continue
        used = fonts.get(layer.id)
        if not used:
            offenders.append("%s: no font record (the text drew nothing?)" % layer.id)
            continue
        fallback = [f for f in used if not f.get("custom")]
        metrics[layer.id] = {"glyphs": sum(f["glyphs"] for f in used),
                             "fallback_glyphs": sum(f["glyphs"] for f in fallback)}
        if fallback:
            offenders.append("%s: %s" % (layer.id, ", ".join("%d glyphs in %s" % (f["glyphs"], f["family"])
                                                           for f in fallback)))
    if offenders:
        return CheckResult(ID, False, SEVERITY, "system font fallback: %s" % "; ".join(offenders), FIXABLE, metrics,
                           offenders=offenders)
    return CheckResult(ID, True, SEVERITY, "all %d glyphs in the kit's fonts" % sum(
        v["glyphs"] for v in metrics.values()), FIXABLE, metrics)
