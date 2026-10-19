"""scene.history: meshes carry no construction history.

Construction history = the chain of modelling operations Maya keeps alive
upstream of a mesh (polyCube -> polyBevel -> polyTriangulate...). Each node
re-evaluates whenever anything upstream changes, so:
  * an edit to an early node silently changes the final mesh,
  * scripts and fixes see a mesh that can change under them,
  * files get heavier and slower to open, which multiplies across a
    40-game catalogue.

Only modelling nodes count: every one of them inherits from Maya's
`polyBase` type. Deformers (skinCluster, blendShape: `geometryFilter`) are
legitimate history on skinned meshes, and groupId/groupParts are just how
Maya stores per-face material assignment. Those are ignored.
"""
from __future__ import annotations

from maya import cmds

from validate.core.result import CheckResult, Severity
from validate.maya.meshes import mesh_shape, plural

ID = "scene.history"
SEVERITY = Severity.ERROR
FIXABLE = True  # see validate/maya/fixes/scene_history.py
DESCRIPTION = "Meshes have no construction (modelling) history; deformers are allowed."


def _construction_history(transform: str):
    nodes = cmds.listHistory(mesh_shape(transform), pruneDagObjects=True) or []
    return sorted(n for n in nodes if "polyBase" in (cmds.nodeType(n, inherited=True) or []))


def run(ctx) -> CheckResult:
    per_mesh = {}
    for mesh in ctx.meshes:
        found = _construction_history(mesh)
        if found:
            per_mesh[mesh] = found

    passed = not per_mesh
    if passed:
        message = "no construction history"
    else:
        total = sum(len(v) for v in per_mesh.values())
        first = sorted(per_mesh)[0]
        message = "%s on %s (e.g. %s: %s)" % (
            plural(total, "construction history node"), plural(len(per_mesh), "mesh", "meshes"),
            first, ", ".join(per_mesh[first]))
    return ctx.result(ID, SEVERITY, FIXABLE, passed=passed, message=message,
                      metrics={"meshes_with_history": len(per_mesh), "history_nodes": per_mesh},
                      offenders=sorted(per_mesh))
