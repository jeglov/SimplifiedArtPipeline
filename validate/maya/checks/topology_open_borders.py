"""topology.open_borders: the mesh is watertight (no border edges).

A border edge has only one face, i.e. it lies on the rim of a hole. Holes
show through as missing geometry from some camera angles and break
anything that needs a closed volume (outline shaders using inverted hulls,
voxelisation, physics mesh colliders, 3D printing of merch...).

Default severity is error. env_module overrides it to warning in
mesh_budgets.yaml: kit pieces delete unseen backfaces on purpose.
"""
from __future__ import annotations

import maya.api.OpenMaya as om

from validate.core.result import CheckResult, Severity
from validate.maya.meshes import component_names, mesh_fn, plural

ID = "topology.open_borders"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "The mesh is watertight: no border edges (holes)."


def run(ctx) -> CheckResult:
    edges, affected = [], 0
    for mesh in ctx.meshes:
        it = om.MItMeshEdge(mesh_fn(mesh).dagPath())
        border = []
        while not it.isDone():
            if it.onBoundary():
                border.append(it.index())
            it.next()
        edges += component_names(mesh, "e", border)
        affected += bool(border)

    passed = not edges
    message = "all meshes are watertight" if passed else "%s on %s" % (
        plural(len(edges), "border edge"), plural(affected, "mesh", "meshes"))
    return ctx.result(ID, SEVERITY, FIXABLE, passed=passed, message=message,
                      metrics={"border_edges": len(edges), "meshes_affected": affected},
                      offenders=edges)
