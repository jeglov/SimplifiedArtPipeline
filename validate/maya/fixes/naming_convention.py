"""Fix for naming.convention: replace names that are clearly defaults or invalid.

The rule that keeps this safe: only rename a node whose CURRENT name breaks
the convention (pCube1, group1, lambert2). Those were never anyone's
intent. A VALID name that just disagrees with the file name (mesh SM_Crate
in SM_FileMismatch.ma) is ambiguous: maybe the mesh is right and the file
is misnamed. That goes to a human.

The file name is the target: the asset is addressed by its file.

Fixable:
  * the single root is a mesh with an invalid name  -> rename to <file stem>
  * the single root is a group with an invalid name -> rename to <file stem>
  * exactly one material with an invalid name       -> rename to M_<AssetName>
Everything else (bad file name, wrong prefix, mixed LOD suffixes, extra
roots, several roots, bad names below the root group, Maya's undeletable
default lambert1) is blocked with the reason.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from maya import cmds

from validate.core.result import FixResult
from validate.maya.meshes import hierarchy, roots, short_name

ID = "naming.convention"
DESCRIPTION = "Rename default/invalid mesh, root group and material names after the file."

_FIXABLE_PROBLEMS = {"mesh_names", "mesh_base", "root_name", "group_names", "material_names"}


def _plan(ctx, result) -> Tuple[List[Tuple[str, str]], Optional[str]]:
    """([(node, new_name)], reason a human is needed)."""
    naming = ctx.conventions.naming
    stem = Path(ctx.source_path).stem
    other = sorted(set(result.metrics.get("problems", {})) - _FIXABLE_PROBLEMS)
    if other:
        return [], "needs a human: %s" % ", ".join(other)

    renames = []
    root = roots(ctx)[0]
    if root in ctx.meshes:  # single mesh, no group
        kind = "mesh_node"
        below = []
    else:
        kind = "group_node"
        below = [n for n in hierarchy(ctx) if n != root]
        bad_below = [n for n in below
                     if not naming.check("mesh_node" if n in ctx.meshes else "group_node", short_name(n)).ok
                     or (n in ctx.meshes and naming.strip_lod(short_name(n)) != stem)]
        if bad_below:
            return [], "names below the root group need a human (e.g. %s: which LOD is it?)" % bad_below[0]
    name = short_name(root)
    if name != stem:
        if naming.check(kind, name).ok:
            return [], ("%r is a valid name but the file is %r: unclear which one is right"
                        % (name, stem))
        renames.append((root, stem))

    bad_mats = [m for m in result.metrics.get("materials", []) if not naming.check("material", m).ok]
    if len(bad_mats) > 1:
        return [], "several badly named materials (%s): which is which?" % ", ".join(bad_mats)
    if bad_mats:
        target = "M_" + stem.split("_", 1)[1]
        if bad_mats[0] == "lambert1":
            return [], "lambert1 is Maya's default material and can't be renamed: assign %s" % target
        if cmds.objExists(target):
            return [], "can't rename %s: %s already exists" % (bad_mats[0], target)
        renames.append((bad_mats[0], target))
    return renames, None if renames else "nothing this fix can change"


def blocker(ctx, result) -> Optional[str]:
    return _plan(ctx, result)[1]


def apply(ctx, result) -> FixResult:
    renames, _ = _plan(ctx, result)
    done = []
    for node, new in renames:
        got = cmds.rename(node, new)
        # Maya silently appends a number on a clash ("SM_Crate1"). It may also
        # return a full path ("|SM_Crate") when a child has the same short name.
        if short_name(got) != new:
            raise RuntimeError("renaming %s to %s gave %s" % (node, new, got))
        done.append("%s -> %s" % (short_name(node), new))
    return FixResult(ID, applied=True, changed=[n for n, _ in renames], message="renamed " + ", ".join(done))
