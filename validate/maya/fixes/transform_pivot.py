"""Fix for transform.pivot: reset pivots and move the geometry onto its anchor.

The class pivot mode (bottom_center / center) defines exactly where the
geometry must sit, so moving it there is fully determined by the
convention: the shape doesn't change, only its placement. The offset moved
is written in the fix message so a human can see it.

Blocked when:
  * transforms aren't frozen: transform.frozen's fix runs first; if it was
    blocked (e.g. negative scale), moving geometry under an unfrozen
    transform would just add to the mess.
  * skinned meshes: moving the mesh without its skeleton breaks the rig.
"""
from __future__ import annotations

from typing import Optional

from maya import cmds

from validate.core.result import FixResult
from validate.maya.checks import transform_frozen
from validate.maya.fixes._common import cm_to_ui, reset_pivots, skinned
from validate.maya.meshes import hierarchy, plural, roots

ID = "transform.pivot"
DESCRIPTION = "Reset pivots to the origin and move the geometry so its class anchor sits there."


def blocker(ctx, result) -> Optional[str]:
    bound = skinned(ctx.meshes)
    if bound:
        return "skinned mesh(es) %s: move the rig and mesh together in the source scene" % ", ".join(bound)
    if not transform_frozen.run(ctx).passed:
        return "transforms aren't frozen (see transform.frozen)"
    return None


def apply(ctx, result) -> FixResult:
    m = result.metrics
    parts, changed = [], list(result.metrics.get("pivots_off_origin", {}))
    if m["anchor_cm"] is not None and m["anchor_offset_cm"] > m["tolerance_cm"]:
        offset = [-v for v in m["anchor_cm"]]
        root_nodes = roots(ctx)
        cmds.move(*[cm_to_ui(v) for v in offset], *root_nodes, relative=True, worldSpace=True)
        cmds.makeIdentity(root_nodes, apply=True, translate=True, normal=0)
        parts.append("moved geometry by %s cm onto the origin (%s mode)"
                     % ([round(v, 3) + 0.0 for v in offset], m["mode"]))  # + 0.0: no "-0.0"
        changed += root_nodes
    reset_pivots(hierarchy(ctx))
    if m["pivots_off_origin"]:
        parts.append("reset %s to the origin" % plural(len(m["pivots_off_origin"]), "pivot"))
    return FixResult(ID, applied=True, changed=changed, message="; ".join(parts) or "reset pivots")
