"""scene.units: the scene's linear unit matches the conventions (cm).

Severity is warning, not error: Unity's "Convert Units" reads the unit
from the FBX and scales correctly either way. The cost of a mixed team
(some files in m, some in cm) is everything AROUND the asset: grid snapping
of modular kits, tool scripts that assume cm, numbers in reviews that don't
compare. The actual size is checked by transform.size, in cm, whatever the
scene unit is.

Only .ma/.mb files can be checked: they store their unit and Maya restores
it on open. When Maya opens an FBX it converts the file to the scene's
preferred unit, so the original unit is lost (backlog #4: read the FBX
header instead). Up-axis isn't checked: Maya doesn't store it in .ma files
at all, it's a user preference.
"""
from __future__ import annotations

from maya import cmds

from validate.core.result import CheckResult, Severity

ID = "scene.units"
SEVERITY = Severity.WARNING
FIXABLE = True  # see validate/maya/fixes/scene_units.py
DESCRIPTION = "The scene's linear unit matches the conventions (cm). Not verifiable for FBX yet."


def run(ctx) -> CheckResult:
    expected = ctx.conventions.linear_unit
    if ctx.source_path.lower().endswith(".fbx"):
        return ctx.result(ID, SEVERITY, FIXABLE, passed=True,
                          message="not verifiable for FBX yet (backlog #4); transform.size still catches unit mistakes",
                          metrics={"verified": False, "expected": expected})

    unit = cmds.currentUnit(query=True, linear=True)
    passed = unit == expected
    message = ("scene unit is %s" % unit) if passed else (
        "scene unit is %s, conventions use %s (size is still checked in cm by transform.size)" % (unit, expected))
    return ctx.result(ID, SEVERITY, FIXABLE, passed=passed, message=message,
                      metrics={"verified": True, "linear_unit": unit, "expected": expected})
