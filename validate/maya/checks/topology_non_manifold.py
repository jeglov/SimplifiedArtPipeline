"""topology.non_manifold: no edge shared by 3+ faces, no "bowtie" vertices.

Manifold = every edge touches at most 2 faces and the faces around each
vertex form one connected fan, like a real-world surface. Non-manifold
geometry breaks tangent generation (normal maps shade wrong), UV unwrapping,
lightmap packing in Unity, and most mesh algorithms (decimation for LODs,
booleans, remeshing in AI clean-up steps).
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity
from validate.maya.meshes import plural, poly_info

ID = "topology.non_manifold"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "No edges shared by more than two faces and no bowtie vertices."


def run(ctx) -> CheckResult:
    edges, verts, affected = [], [], 0
    for mesh in ctx.meshes:
        e = poly_info(mesh, "nonManifoldEdges")
        v = poly_info(mesh, "nonManifoldVertices")
        edges += e
        verts += v
        affected += bool(e or v)

    passed = not (edges or verts)
    if passed:
        message = "no non-manifold geometry"
    else:
        # Only mention what was found: "1 non-manifold vertex", not "0 edges and 1 vertex".
        parts = []
        if edges:
            parts.append(plural(len(edges), "non-manifold edge"))
        if verts:
            parts.append(plural(len(verts), "non-manifold vertex", "non-manifold vertices"))
        message = "%s on %s" % (" and ".join(parts), plural(affected, "mesh", "meshes"))
    return ctx.result(ID, SEVERITY, FIXABLE, passed=passed, message=message,
                      metrics={"nonmanifold_edges": len(edges),
                               "nonmanifold_vertices": len(verts),
                               "meshes_affected": affected},
                      offenders=edges + verts)
