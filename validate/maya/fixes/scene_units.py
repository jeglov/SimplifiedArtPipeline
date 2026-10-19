"""Fix for scene.units: set the scene's linear unit to the convention (cm).

Invisible by construction: Maya always stores geometry in cm internally;
the scene unit only changes how values are displayed and which unit the
file declares. The asset's size is exactly the same afterwards.
"""
from __future__ import annotations

from typing import Optional

from maya import cmds

from validate.core.result import FixResult

ID = "scene.units"
DESCRIPTION = "Set the scene's linear unit to the conventions unit (geometry is unchanged)."


def blocker(ctx, result) -> Optional[str]:
    return None


def apply(ctx, result) -> FixResult:
    before = cmds.currentUnit(query=True, linear=True)
    expected = ctx.conventions.linear_unit
    cmds.currentUnit(linear=expected)
    return FixResult(ID, applied=True, message="scene unit %s -> %s (geometry unchanged)" % (before, expected))
