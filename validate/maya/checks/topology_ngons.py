"""topology.ngons: no faces with more than 4 sides.

The GPU only draws triangles, so every ngon gets triangulated somewhere.
If Maya and Unity triangulate it differently (they can), the in-game mesh
shades differently from what the artist saw, and a non-planar or concave
ngon can even fold over itself. Quads are safe: there are only two ways to
split them and both are predictable.
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity
from validate.maya.meshes import component_names, mesh_fn, plural

ID = "topology.ngons"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "No polygons with more than 4 sides."


def run(ctx) -> CheckResult:
    faces, affected, max_sides = [], 0, 0
    for mesh in ctx.meshes:
        # getVertices() -> (vertex count per face, flat vertex id list).
        # Reading the counts array is one call instead of a per-face loop.
        counts = mesh_fn(mesh).getVertices()[0]
        ngons = [i for i, n in enumerate(counts) if n > 4]
        if ngons:
            max_sides = max(max_sides, max(counts[i] for i in ngons))
        faces += component_names(mesh, "f", ngons)
        affected += bool(ngons)

    passed = not faces
    message = "no ngons" if passed else "%s (up to %d sides) on %s" % (
        plural(len(faces), "ngon"), max_sides, plural(affected, "mesh", "meshes"))
    return ctx.result(ID, SEVERITY, FIXABLE, passed=passed, message=message,
                      metrics={"ngons": len(faces), "max_sides": max_sides,
                               "meshes_affected": affected},
                      offenders=faces)
