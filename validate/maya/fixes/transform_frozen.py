"""Fix for transform.frozen: freeze transforms, then reset pivots to the origin.

Freezing bakes translate/rotate/scale/shear into the vertices: the mesh
stays exactly where it is, its transform becomes identity. Pivots are reset
afterwards because freezing leaves them behind in world space.

Blocked when it isn't invisible:
  * negative scale: Maya freezes it by flipping the stored normals and
    setting the shape's hidden `opposite` flag, which FBX doesn't carry,
    so the mesh imports into Unity inside out. Mirror it properly instead.
  * skinned meshes: freezing a bound mesh breaks its bind pose.
"""
from __future__ import annotations

from typing import Optional

from maya import cmds

from validate.core.result import FixResult
from validate.maya.fixes._common import reset_pivots, skinned
from validate.maya.meshes import hierarchy, plural, roots

ID = "transform.frozen"
DESCRIPTION = "Freeze transforms on the asset hierarchy and reset pivots to the origin."


def blocker(ctx, result) -> Optional[str]:
    negative = sorted(n for n, v in result.metrics.get("values", {}).items()
                      if any(s < 0 for s in v.get("scale", [])))
    if negative:
        return ("negative scale on %s: freezing would flip the normals (imports inside out "
                "in Unity); mirror the geometry in the source scene" % ", ".join(negative))
    bound = skinned(ctx.meshes)
    if bound:
        return "skinned mesh(es) %s: freezing would break the bind pose" % ", ".join(bound)
    return None


def apply(ctx, result) -> FixResult:
    # Freezing the roots pushes the freeze down the whole hierarchy.
    cmds.makeIdentity(roots(ctx), apply=True, translate=True, rotate=True, scale=True, normal=0)
    reset_pivots(hierarchy(ctx))
    nodes = list(result.offenders)
    return FixResult(ID, applied=True, changed=nodes,
                     message="froze %s and reset pivots to the origin" % plural(len(nodes), "transform"))
