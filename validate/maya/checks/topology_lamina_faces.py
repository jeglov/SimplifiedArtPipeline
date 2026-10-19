"""topology.lamina_faces: no two faces sharing all of their edges.

A lamina face is a duplicate stacked on another face (often left by a bad
extrude, a "combine" without cleanup, or an AI mesh generator). It costs
triangles for nothing and z-fights (flickers) when rendered.
polyInfo reports only the extra face of each pair, so the offenders are
exactly the faces a fix would delete.
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity
from validate.maya.meshes import plural, poly_info

ID = "topology.lamina_faces"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "No duplicate faces that share all of their edges with another face."


def run(ctx) -> CheckResult:
    faces, affected = [], 0
    for mesh in ctx.meshes:
        found = poly_info(mesh, "laminaFaces")
        faces += found
        affected += bool(found)

    passed = not faces
    message = "no lamina faces" if passed else "%s on %s" % (
        plural(len(faces), "lamina face"), plural(affected, "mesh", "meshes"))
    return ctx.result(ID, SEVERITY, FIXABLE, passed=passed, message=message,
                      metrics={"lamina_faces": len(faces), "meshes_affected": affected},
                      offenders=faces)
