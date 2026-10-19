"""transform.frozen: every mesh and parent group has translate 0, rotate 0,
scale 1 and no shear.

Unity imports each Maya transform as a GameObject with those values. An
unfrozen crate arrives as a prefab with rotation (0, 90, 0) and scale 1.2:
designers place it, reset its transform out of habit, and it changes size
and orientation. Negative scale is worse: it flips the triangle winding, so
the mesh renders inside out unless the importer compensates.

Groups are checked too, not just meshes (see meshes.hierarchy).
"""
from __future__ import annotations

from maya import cmds

from validate.core.result import CheckResult, Severity
from validate.maya.meshes import hierarchy, plural

ID = "transform.frozen"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "Meshes and their parent groups have frozen transforms (t=0, r=0, s=1, no shear)."

TOLERANCE = 1e-4
IDENTITY = (("translate", 0.0), ("rotate", 0.0), ("scale", 1.0), ("shear", 0.0))


def _non_identity(node: str):
    """{"rotate": [0.0, 90.0, 0.0], ...} for every channel that isn't identity."""
    found = {}
    for attr, identity in IDENTITY:
        values = list(cmds.getAttr("%s.%s" % (node, attr))[0])
        if any(abs(v - identity) > TOLERANCE for v in values):
            found[attr] = values
    return found


def run(ctx) -> CheckResult:
    nodes = hierarchy(ctx)
    unfrozen = {}
    for node in nodes:
        values = _non_identity(node)
        if values:
            unfrozen[node] = values
    negative = sorted(n for n, v in unfrozen.items() if any(s < 0 for s in v.get("scale", [])))

    passed = not unfrozen
    if passed:
        message = "all %s frozen" % plural(len(nodes), "transform")
    else:
        first = sorted(unfrozen)[0]
        message = "%s not frozen (e.g. %s: %s)" % (
            plural(len(unfrozen), "transform"), first,
            ", ".join("%s %s" % (k, [round(x, 3) for x in v]) for k, v in sorted(unfrozen[first].items())))
        if negative:
            message += "; %s with NEGATIVE scale (renders inside out)" % plural(len(negative), "transform")
    return ctx.result(ID, SEVERITY, FIXABLE, passed=passed, message=message,
                      metrics={"transforms_checked": len(nodes), "not_frozen": len(unfrozen),
                               "negative_scale": len(negative), "values": unfrozen},
                      offenders=sorted(unfrozen))
