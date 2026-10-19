"""Helpers shared by several fixes (not a fix itself: not in the registry)."""
from __future__ import annotations

from typing import Iterable, List

import maya.api.OpenMaya as om
from maya import cmds

from validate.maya.meshes import is_skinned


def reset_pivots(nodes: Iterable[str]) -> None:
    """Put rotate and scale pivots of every node at the world origin.

    Needed after any freeze: makeIdentity leaves pivots where they were in
    world space (probed: a frozen child kept its pivot at (5, 0, 0)).
    """
    for node in nodes:
        cmds.xform(node, pivots=(0, 0, 0), worldSpace=True)


def cm_to_ui(value_cm: float) -> float:
    """cmds.move/xform take UI units; our metrics are always cm."""
    return om.MDistance(value_cm, om.MDistance.kCentimeters).asUnits(om.MDistance.uiUnit())


def skinned(meshes: Iterable[str]) -> List[str]:
    return sorted(m for m in meshes if is_skinned(m))
