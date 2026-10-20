"""kit.slots: the kit fills every slot the template declares, in every locale asked for.

An empty slot is the classic template failure: a banner ships with
"{headline}" or a blank button. Checked before rendering, so the render
never starts on a kit that can't produce a complete banner. Whitespace-only
strings count as empty. Colour slots must name a palette entry, font slots a
font in banners/fonts/fonts.yaml.
"""
from __future__ import annotations

from banners.core.checks.context import plural
from validate.core.result import CheckResult, Severity

ID = "kit.slots"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "Every template slot is filled by the kit, in every locale."


def run(ctx) -> CheckResult:
    t, k = ctx.template, ctx.kit
    missing = []
    for slot in t.slots["colour"]:
        name = k.colours.get(slot)
        if name is None:
            missing.append("colour.%s" % slot)
        elif name not in k.palette:
            missing.append("colour.%s (palette has no %r)" % (slot, name))
    for slot in t.slots["font"]:
        fid = k.fonts.get(slot)
        if fid is None:
            missing.append("font.%s" % slot)
        elif fid not in ctx.fonts:
            missing.append("font.%s (no font %r in fonts.yaml)" % (slot, fid))
    missing += ["image.%s" % s for s in t.slots["image"] if s not in k.images]
    for locale in ctx.locales:
        strings = k.strings.get(locale)
        if strings is None:
            missing.append("strings.%s" % locale)
            continue
        missing += ["text.%s [%s]" % (s, locale) for s in t.slots["text"] if not strings.get(s, "").strip()]
    total = sum(len(v) for v in t.slots.values()) + len(t.slots["text"]) * (len(ctx.locales) - 1)
    if missing:
        return CheckResult(ID, False, SEVERITY, "%s of %d empty: %s" % (plural(len(missing), "slot"), total,
                                                                        ", ".join(missing[:5])),
                           FIXABLE, {"slots": total, "empty": len(missing)}, offenders=missing)
    return CheckResult(ID, True, SEVERITY, "all %d slots filled (%s)" % (total, ", ".join(ctx.locales)),
                       FIXABLE, {"slots": total, "empty": 0})
