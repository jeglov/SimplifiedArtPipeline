"""topology.inverted: what Unity receives faces outward, and matches what Maya shows.

Two ways a mesh ships inside out while it can look fine in Maya (probed on
Maya 2023 / FBX 2020.3.1):

  * The shape's `opposite` flag. Maya renders the mesh flipped; FBX doesn't
    carry the flag at all (the exported file is byte-for-byte the same with
    or without it). Freezing a negative scale sets it: Maya mirrors the
    points, leaves the winding, and sets `opposite` to hide the result, so
    Unity gets inward-facing triangles.
  * Reversed winding (Normals > Reverse, a bad mirror or combine). The
    viewport's default double-sided lighting hides it; Unity culls back
    faces, so the outside disappears.

Winding is measured, not guessed, on CLOSED meshes: the signed volume of
the triangles (object space) is negative when they face inward. An open
mesh has no inside, so only its `opposite` flag can be checked; a flat
closed mesh (e.g. stacked lamina faces) has no volume and is skipped too.
A mesh with one flipped shell among several can still have a positive
total volume (backlog #43).
"""
from __future__ import annotations

import maya.api.OpenMaya as om
from maya import cmds

from validate.core.result import CheckResult, Severity
from validate.maya.meshes import mesh_fn, mesh_shape, plural

ID = "topology.inverted"
SEVERITY = Severity.ERROR
FIXABLE = True  # see validate/maya/fixes/topology_inverted.py
DESCRIPTION = "Faces point outward in the exported file (closed meshes) and no shape has the `opposite` flag."

# |volume| below this fraction of the bounding-box cube counts as flat.
FLAT = 1e-6


def winding(mesh: str) -> str:
    """"outward", "inward", "open" (has border edges) or "flat" (no volume)."""
    fn = mesh_fn(mesh)
    it = om.MItMeshEdge(fn.dagPath())
    while not it.isDone():
        if it.onBoundary():
            return "open"
        it.next()
    points = fn.getPoints(om.MSpace.kObject)
    ids = fn.getTriangles()[1]
    volume = 0.0
    for i in range(0, len(ids), 3):
        a, b, c = (om.MVector(points[ids[i + k]]) for k in range(3))
        volume += a * (b ^ c)  # 6x the signed volume of the tetrahedron (origin, a, b, c)
    box = fn.boundingBox
    extent = max(box.width, box.height, box.depth)
    if abs(volume) <= 6 * FLAT * extent ** 3:
        return "flat"
    return "outward" if volume > 0 else "inward"


def opposite(mesh: str) -> bool:
    return bool(cmds.getAttr(mesh_shape(mesh) + ".opposite"))


def run(ctx) -> CheckResult:
    found, unverified = {}, 0
    for mesh in ctx.meshes:
        state = {"opposite": opposite(mesh), "winding": winding(mesh)}
        unverified += state["winding"] in ("open", "flat")
        if state["opposite"] or state["winding"] == "inward":
            found[mesh] = state
    flagged = sum(s["opposite"] for s in found.values())
    inward = sum(s["winding"] == "inward" for s in found.values())

    passed = not found
    if passed:
        message = "no inverted meshes"
    else:
        parts = []
        if inward:
            parts.append("%s facing inward" % plural(inward, "closed mesh", "closed meshes"))
        if flagged:
            parts.append("%s with the `opposite` flag (FBX drops it: Unity and Maya disagree)"
                         % plural(flagged, "mesh", "meshes"))
        message = "; ".join(parts)
    if unverified:
        message += "; winding not verifiable on %s (open or flat)" % plural(unverified, "mesh", "meshes")
    return ctx.result(ID, SEVERITY, FIXABLE, passed=passed, message=message,
                      metrics={"meshes_checked": len(ctx.meshes), "inward": inward, "opposite_flag": flagged,
                               "not_verifiable": unverified, "meshes": found},
                      offenders=sorted(found))
