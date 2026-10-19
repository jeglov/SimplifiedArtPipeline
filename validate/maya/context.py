"""CheckContext: everything a check needs, gathered once per file.

Checks receive this object instead of querying "the scene" themselves for
shared facts (which meshes exist, which asset class applies). That keeps
every check small and guarantees they all look at the same list of meshes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Tuple

from validate.core.conventions import AssetClass, Conventions
from validate.core.result import CheckResult, Severity


@dataclass(frozen=True)
class CheckContext:
    source_path: str
    conventions: Conventions
    asset_class: AssetClass
    # Full DAG paths of transforms that own a (non-intermediate) mesh shape,
    # sorted. Full paths ("|SM_Crate|SM_Crate_LOD0") because short names are
    # not unique in Maya.
    meshes: Tuple[str, ...]

    def result(self, check_id: str, default_severity: Severity, fixable: bool, passed: bool,
               message: str, metrics: Optional[Dict[str, Any]] = None,
               offenders: Sequence[str] = ()) -> CheckResult:
        """Build a CheckResult with the asset class's severity override applied.

        Checks always go through here, so no check can forget the override
        (e.g. topology.open_borders is only a warning for env_module).
        """
        return CheckResult(
            id=check_id,
            passed=passed,
            severity=self.asset_class.severity_for(check_id, default_severity),
            message=message,
            fixable=fixable,
            metrics=metrics or {},
            offenders=offenders,
        )


def build(source_path: str, conventions: Conventions, asset_class: AssetClass) -> CheckContext:
    """Collect scene facts from the currently open Maya scene."""
    from maya import cmds

    # noIntermediate skips hidden "orig" shapes left behind by deformers/history.
    shapes = cmds.ls(type="mesh", long=True, noIntermediate=True) or []
    transforms = set()
    for shape in shapes:
        transforms.update(cmds.listRelatives(shape, parent=True, fullPath=True) or [])

    return CheckContext(
        source_path=source_path,
        conventions=conventions,
        asset_class=asset_class,
        meshes=tuple(sorted(transforms)),
    )
