"""kit.colours: palette entries are #RRGGBB.

Opaque sRGB hex only: an alpha colour or a CSS name would render, but the
contrast checks compute WCAG luminance from these values, and a colour the
checks can't read is a colour they can't judge.
"""
from __future__ import annotations

import re

from banners.core.checks.context import plural
from validate.core.result import CheckResult, Severity

ID = "kit.colours"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "Every palette colour is an opaque #RRGGBB value."
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def run(ctx) -> CheckResult:
    bad = ["%s: %r" % (name, value) for name, value in sorted(ctx.kit.palette.items()) if not HEX.match(value)]
    n = len(ctx.kit.palette)
    if not n:
        return CheckResult(ID, False, SEVERITY, "the palette is empty", FIXABLE, {"colours": 0, "bad": 0},
                           offenders=["palette"])
    if bad:
        return CheckResult(ID, False, SEVERITY, "%s of %d not #RRGGBB: %s" % (plural(len(bad), "colour"), n, bad[0]),
                           FIXABLE, {"colours": n, "bad": len(bad)}, offenders=bad)
    return CheckResult(ID, True, SEVERITY, "%d palette colours" % n, FIXABLE, {"colours": n, "bad": 0})
