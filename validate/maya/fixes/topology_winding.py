"""Fix for topology.winding: conform each shell, delete interior shells, turn inside-out shells outward.

Probed on Maya 2023:
  * polyNormal normalMode=2 (Conform) flips the minority faces of each
    shell to match the majority, without splitting vertices.
  * Conform leaves a consistent but inward shell of a combined mesh as it
    is; reversing that shell's faces (normalMode=0 on the face list) fixes
    it, and with userNormalMode=1 the locked normals follow the faces.

Interior shells (inward AND enclosed by an outward shell: the inner wall of
a hollow remesh) are deleted, not reversed: reversing would leave invisible
triangles in the budget. A first version did reverse them, and a generated
gem shipped 118 hidden triangles; validate.core.winding.classify_inward now
tells the two apart.

Blocked on skinned meshes (needs history under the skinCluster) and on
non-orientable shells (no consistent winding exists).
"""
from __future__ import annotations

from typing import Optional

from maya import cmds

from validate.core.result import FixResult
from validate.maya.checks.topology_winding import analyse
from validate.maya.meshes import is_skinned, plural

ID = "topology.winding"
DESCRIPTION = "Conform each shell to its majority, delete interior shells, reverse inside-out shells."


def blocker(ctx, result) -> Optional[str]:
    meshes = sorted(result.metrics["meshes"])
    bound = [m for m in meshes if is_skinned(m)]
    if bound:
        return "%s skinned (%s): fix the winding before skinning" % (
            plural(len(bound), "mesh is", "meshes are"), ", ".join(bound))
    if result.metrics["non_orientable_shells"]:
        return "non-orientable shells have no consistent winding: rebuild that geometry"
    return None


def apply(ctx, result) -> FixResult:
    conformed, deleted, reversed_shells = [], 0, 0
    for mesh in sorted(result.metrics["meshes"]):
        if result.metrics["meshes"][mesh]["flipped_faces"]:
            cmds.polyNormal(mesh, normalMode=2, userNormalMode=1, constructionHistory=False)
            conformed.append(mesh)
        interior = analyse(mesh)["interior"]  # after conforming: shells are consistent now
        if interior:
            cmds.delete(["%s.f[%d]" % (mesh, f) for shell in interior for f in shell])
            cmds.delete(mesh, constructionHistory=True)
            deleted += len(interior)
        inward = analyse(mesh)["inward"]  # again: deleting renumbers the faces
        if inward:
            cmds.polyNormal(["%s.f[%d]" % (mesh, f) for shell in inward for f in shell],
                            normalMode=0, userNormalMode=1, constructionHistory=False)
            reversed_shells += len(inward)
    parts = []
    if conformed:
        parts.append("conformed %s" % plural(len(conformed), "mesh", "meshes"))
    if deleted:
        parts.append("deleted %s" % plural(deleted, "interior shell", "interior shells"))
    if reversed_shells:
        parts.append("reversed %s" % plural(reversed_shells, "inside-out shell", "inside-out shells"))
    return FixResult(ID, applied=True, changed=sorted(result.metrics["meshes"]), message=", ".join(parts))
