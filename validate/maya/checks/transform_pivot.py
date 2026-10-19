"""transform.pivot: pivots are at the world origin and the geometry sits
correctly around it, per the class's pivot mode.

Unity does NOT import Maya pivots: in Unity the pivot of an object IS the
origin of its transform. With frozen transforms that origin is the world
origin (0,0,0). So what decides where a crate "stands" in Unity is where
the GEOMETRY sits relative to the origin:

  bottom_center  bbox bottom-centre at the origin: props and characters
                 stand on the ground where the designer drops them
  center         bbox centre at the origin: things that spin (pickups)
  origin         no geometry rule: modular kit pieces snap by a corner

Maya pivots are checked too: a pivot left away from the origin (freezing
a group leaves children's pivots behind!) means the artist sees a
different pivot in Maya than the designer gets in Unity.
"""
from __future__ import annotations

import maya.api.OpenMaya as om

from validate.core.result import CheckResult, Severity
from validate.maya.meshes import hierarchy, plural, roots, transform_fn, world_bbox, xyz

ID = "transform.pivot"
SEVERITY = Severity.ERROR
FIXABLE = True  # see validate/maya/fixes/transform_pivot.py
DESCRIPTION = "Pivots are at the world origin and the geometry is placed per the class pivot mode."


def _anchor(mode: str, box: om.MBoundingBox):
    """The point of the bbox that must sit at the origin, or None for mode 'origin'."""
    c = box.center
    if mode == "bottom_center":
        return om.MPoint(c.x, box.min.y, c.z)
    if mode == "center":
        return c
    return None


def run(ctx) -> CheckResult:
    rules = ctx.asset_class.pivot
    tol = rules.tolerance_cm
    origin = om.MPoint(0, 0, 0)

    # 1. Maya pivots of every transform in the hierarchy.
    bad_pivots = {}
    for node in hierarchy(ctx):
        fn = transform_fn(node)
        rp = fn.rotatePivot(om.MSpace.kWorld)
        sp = fn.scalePivot(om.MSpace.kWorld)
        if rp.distanceTo(origin) > tol or sp.distanceTo(origin) > tol:
            bad_pivots[node] = {"rotate_pivot_cm": xyz(rp), "scale_pivot_cm": xyz(sp)}

    # 2. Geometry placement around the origin.
    box = world_bbox(ctx.meshes)
    anchor = _anchor(rules.mode, box)
    offset = anchor.distanceTo(origin) if anchor is not None else 0.0
    geometry_ok = offset <= tol

    problems = []
    if not geometry_ok:
        problems.append("geometry %s is at %s cm, must be at the origin (%s mode for %s, tolerance %g cm)" % (
            rules.mode.replace("_", "-"), [round(v, 2) for v in xyz(anchor)],
            rules.mode, ctx.asset_class.name, tol))
    if bad_pivots:
        first = sorted(bad_pivots)[0]
        problems.append("%s with a pivot away from the origin (e.g. %s at %s cm)" % (
            plural(len(bad_pivots), "transform"), first,
            [round(v, 2) for v in bad_pivots[first]["rotate_pivot_cm"]]))

    passed = not problems
    if passed:
        message = "pivots at origin, geometry placed for %s mode" % rules.mode
    else:
        message = "; ".join(problems)
    metrics = {"mode": rules.mode, "tolerance_cm": tol,
               "bbox_min_cm": xyz(box.min), "bbox_max_cm": xyz(box.max),
               "anchor_cm": xyz(anchor) if anchor is not None else None,
               "anchor_offset_cm": offset, "pivots_off_origin": bad_pivots}
    offenders = sorted(bad_pivots) + ([] if geometry_ok else roots(ctx))
    return ctx.result(ID, SEVERITY, FIXABLE, passed=passed, message=message,
                      metrics=metrics, offenders=offenders)
