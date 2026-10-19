"""scene.has_meshes: the file contains at least one polygon mesh.

Runs first. If it fails, every other check would pass trivially on an empty
scene ("0 ngons!"), so this is what stops an empty export from being
reported as a clean asset.
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity

ID = "scene.has_meshes"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "The file contains at least one polygon mesh."


def run(ctx) -> CheckResult:
    count = len(ctx.meshes)
    if count:
        message = "%d mesh(es) found" % count
    else:
        message = "no polygon meshes in file (empty export, or only NURBS/curves?)"
    return ctx.result(ID, SEVERITY, FIXABLE, passed=count > 0, message=message,
                      metrics={"mesh_count": count})
