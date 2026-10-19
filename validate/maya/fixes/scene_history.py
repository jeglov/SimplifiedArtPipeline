"""Fix for scene.history: bake modelling history into the mesh.

Uses bakePartialHistory(prePostDeformers=True), Maya's "Delete Non-Deformer
History": the mesh keeps its current shape, modelling nodes (polyBevel,
polyTriangulate...) are baked away, and deformers (skinCluster, blendShape)
stay. A plain "Delete History" would also delete the skin of a character.
"""
from __future__ import annotations

from typing import Optional

from maya import cmds

from validate.core.result import FixResult
from validate.maya.meshes import mesh_shape, plural

ID = "scene.history"
DESCRIPTION = "Bake construction history into the mesh (deformers are kept)."


def blocker(ctx, result) -> Optional[str]:
    return None  # baking history never changes the visible result


def apply(ctx, result) -> FixResult:
    meshes = list(result.offenders)
    nodes = sum(len(v) for v in result.metrics.get("history_nodes", {}).values())
    for mesh in meshes:
        cmds.bakePartialHistory(mesh_shape(mesh), prePostDeformers=True)
    return FixResult(ID, applied=True, changed=meshes,
                     message="baked %s on %s" % (plural(nodes, "construction history node"),
                                                 plural(len(meshes), "mesh", "meshes")))
