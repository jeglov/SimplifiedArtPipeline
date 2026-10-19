"""transform.size: the asset's largest dimension is plausible for its class.

This is the check that catches UNIT mistakes, whatever the file format: a
crate authored in metres arrives 100x too small (0.6 cm instead of 60 cm).
Very common with AI image-to-3D output, which is usually normalised to
about 1 unit, and with files from tools set to other units. When a known
unit factor would bring the size into range, the message says so, so an
agent knows exactly which rescale to try.
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity
from validate.maya.meshes import roots, world_bbox

ID = "transform.size"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "The largest dimension (cm) is within the asset class size range."

# Real size = value * factor, if the numbers were meant in that unit but read as cm.
UNIT_GUESSES = ((100.0, "metres"), (0.1, "millimetres"), (2.54, "inches"))


def run(ctx) -> CheckResult:
    if not ctx.meshes:  # empty scene: scene.has_meshes reports it, once
        return ctx.result(ID, SEVERITY, FIXABLE, passed=True, message="no meshes to check (see scene.has_meshes)")
    lo, hi = ctx.asset_class.size_cm_min, ctx.asset_class.size_cm_max
    box = world_bbox(ctx.meshes)
    dims = [box.width, box.height, box.depth]
    largest = max(dims)

    passed = lo <= largest <= hi
    if passed:
        message = "largest dimension %.1f cm, within %g-%g cm for %s" % (largest, lo, hi, ctx.asset_class.name)
        hint = None
    else:
        message = "largest dimension %.2f cm, %s expects %g-%g cm" % (largest, ctx.asset_class.name, lo, hi)
        guess = next(((f, u) for f, u in UNIT_GUESSES if lo <= largest * f <= hi), None)
        hint = guess[1] if guess else None
        if guess:
            message += " - looks authored in %s (x%g = %.1f cm)" % (guess[1], guess[0], largest * guess[0])
    return ctx.result(ID, SEVERITY, FIXABLE, passed=passed, message=message,
                      metrics={"dimensions_cm": dims, "largest_cm": largest,
                               "size_cm_min": lo, "size_cm_max": hi, "unit_guess": hint},
                      offenders=[] if passed else roots(ctx))
