"""Fix for topology.inverted: make the stored winding face outward, clear `opposite`.

The FBX only carries the stored winding, so that's what gets corrected:

  * closed mesh: the geometry decides. Reverse it if it faces inward
    (signed volume < 0), then clear the flag.
  * open mesh with the flag: there is no inside to measure, so keep what
    the artist saw in Maya (stored winding flipped by the flag): reverse,
    then clear. The Maya viewport doesn't change; Unity now matches it.

polyNormal must reverse the user (locked) normals too (userNormalMode=1).
Probed: with the default, locked normals stay put, so the faces flip but
the normals don't, and every FBX input has locked normals.

Blocked on skinned meshes: editing the shape below a skinCluster needs
history, which belongs in the rig's source scene.
"""
from __future__ import annotations

from typing import Optional

from maya import cmds

from validate.core.result import FixResult
from validate.maya.meshes import is_skinned, mesh_shape, plural

ID = "topology.inverted"
DESCRIPTION = "Reverse inward-facing meshes and clear the `opposite` flag, keeping what Maya shows on open meshes."


def blocker(ctx, result) -> Optional[str]:
    bound = [m for m in result.offenders if is_skinned(m)]
    if bound:
        return "%s skinned (%s): fix the winding before skinning" % (
            plural(len(bound), "mesh is", "meshes are"), ", ".join(bound))
    return None


def apply(ctx, result) -> FixResult:
    reversed_, cleared = [], []
    for mesh, state in sorted(result.metrics["meshes"].items()):
        closed = state["winding"] in ("outward", "inward")
        if state["winding"] == "inward" or (not closed and state["opposite"]):
            cmds.polyNormal(mesh, normalMode=0, userNormalMode=1, constructionHistory=False)
            reversed_.append(mesh)
        if state["opposite"]:
            cmds.setAttr(mesh_shape(mesh) + ".opposite", False)
            cleared.append(mesh)
    parts = []
    if reversed_:
        parts.append("reversed %s" % plural(len(reversed_), "mesh", "meshes"))
    if cleared:
        parts.append("cleared `opposite` on %s" % plural(len(cleared), "mesh", "meshes"))
    return FixResult(ID, applied=True, changed=list(result.offenders), message=", ".join(parts))
