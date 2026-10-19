"""Fix for topology.lamina_faces: delete the duplicate faces.

The check's offenders are exactly the EXTRA face of each stacked pair
(polyInfo reports one per pair), so deleting them leaves the surface the
artist meant, with no change in shape.

Blocked on skinned meshes: deleting faces downstream of a skinCluster
changes the component order the skin weights were painted on. That fix
belongs in the source scene, before skinning.
"""
from __future__ import annotations

from typing import Optional

from maya import cmds

from validate.core.result import FixResult
from validate.maya.meshes import is_skinned, mesh_shape, plural

ID = "topology.lamina_faces"
DESCRIPTION = "Delete the duplicate (stacked) faces reported by the check."


def _meshes(result):
    return sorted({face.split(".")[0] for face in result.offenders})


def blocker(ctx, result) -> Optional[str]:
    skinned = [m for m in _meshes(result) if is_skinned(m)]
    if skinned:
        return "%s skinned (%s): fix lamina faces before skinning" % (
            plural(len(skinned), "mesh is", "meshes are"), ", ".join(skinned))
    return None


def apply(ctx, result) -> FixResult:
    faces = list(result.offenders)
    cmds.delete(faces)
    for mesh in _meshes(result):  # deleting components can leave a deleteComponent node
        cmds.bakePartialHistory(mesh_shape(mesh), prePostDeformers=True)
    return FixResult(ID, applied=True, changed=faces,
                     message="deleted %s" % plural(len(faces), "lamina face"))
