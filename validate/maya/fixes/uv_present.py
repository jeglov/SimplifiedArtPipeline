"""Fix for uv.present: rename or reorder UV sets, never invent UVs.

Two cases are unambiguous:
  * the mesh has exactly ONE UV set and it has the wrong name (files from
    other tools: "UVChannel_1", "UVMap"): rename it to map1.
  * map1 exists but isn't first: swap it into first place, because Unity
    uses the ORDER (first set = UV0).

Everything else needs a human: faces with no UVs need an unwrap, and with
several sets and no map1 there's no telling which one is the base color UV.
The fix is all-or-nothing: if any mesh needs a human, nothing is changed.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from maya import cmds

from validate.core.result import FixResult
from validate.maya.meshes import mesh_fn, mesh_shape, plural

ID = "uv.present"
DESCRIPTION = "Rename a lone UV set to the required name, or move the required set first."


def _plan(ctx) -> Tuple[List[Tuple[str, str, str, str]], List[str]]:
    """([(action, mesh, set_now, required)], [reasons a human is needed])."""
    required = list(ctx.asset_class.uv.required_sets)
    plan, problems = [], []
    for mesh in ctx.meshes:
        fn = mesh_fn(mesh)
        names = list(fn.getUVSetNames())
        uv_sets = required
        if any(s not in names for s in required):
            if len(required) == 1 and len(names) == 1:
                plan.append(("rename", mesh, names[0], required[0]))
                uv_sets = names
            else:
                problems.append("%s has UV sets %s: can't tell which one should be %s"
                                % (mesh, names, required))
                continue
        elif names[:len(required)] != required:
            if len(required) == 1:
                plan.append(("first", mesh, names[0], required[0]))
            else:
                problems.append("%s: UV set order %s, expected %s first" % (mesh, names, required))
        for uv_set in uv_sets:
            empty = sum(1 for n in fn.getAssignedUVs(uv_set)[0] if n == 0)
            if empty:
                problems.append("%s: %s with no UVs in %s (needs an unwrap)"
                                % (mesh, plural(empty, "face"), uv_set))
    return plan, problems


def blocker(ctx, result) -> Optional[str]:
    plan, problems = _plan(ctx)
    if problems:
        return "; ".join(problems)
    return None if plan else "nothing this fix can change"


def apply(ctx, result) -> FixResult:
    plan, _ = _plan(ctx)
    done = []
    for action, mesh, current, required in plan:
        shape = mesh_shape(mesh)
        if action == "rename":
            cmds.polyUVSet(shape, rename=True, uvSet=current, newUVSet=required)
            done.append("renamed %s's UV set %r to %r" % (mesh, current, required))
        else:  # reorder swaps the two sets
            cmds.polyUVSet(shape, reorder=True, uvSet=required, newUVSet=current)
            done.append("moved %s's UV set %r before %r" % (mesh, required, current))
    return FixResult(ID, applied=True, changed=sorted({p[1] for p in plan}), message="; ".join(done))
