"""The ordered list of checks. This is the full set of tools an agent can call.

Explicit list, not auto-discovery: adding a check is a deliberate one-line
change that shows up in code review.
Order = report order: cheap/fundamental checks first.
"""
from __future__ import annotations

from typing import List, Tuple

from validate.core.result import CheckResult
from validate.maya.checks import (
    budget_lods,
    budget_materials,
    budget_tris,
    scene_has_meshes,
    scene_units,
    topology_lamina_faces,
    topology_ngons,
    topology_non_manifold,
    topology_open_borders,
    transform_frozen,
    transform_pivot,
    transform_size,
    uv_overlap,
    uv_present,
    uv_unit_square,
)

CHECKS = (
    scene_has_meshes,
    # topology
    topology_non_manifold,
    topology_lamina_faces,
    topology_open_borders,
    topology_ngons,
    # budget
    budget_tris,
    budget_lods,
    budget_materials,
    # uv
    uv_present,
    uv_unit_square,
    uv_overlap,
    # transforms & scale
    scene_units,
    transform_frozen,
    transform_pivot,
    transform_size,
)

_REQUIRED = ("ID", "SEVERITY", "FIXABLE", "DESCRIPTION", "run")


def _validate(checks) -> None:
    """Fail at import time if a check module breaks the contract."""
    seen = set()
    for module in checks:
        missing = [name for name in _REQUIRED if not hasattr(module, name)]
        if missing:
            raise TypeError("%s is missing %s" % (module.__name__, missing))
        if module.ID in seen:
            raise ValueError("duplicate check id %s" % module.ID)
        seen.add(module.ID)


_validate(CHECKS)


def run_all(ctx) -> Tuple[List[CheckResult], List[str]]:
    """Run every check. Returns (results, tool_errors).

    A check that crashes is a bug in the TOOL, not in the asset. It's
    recorded as a tool error and the other checks still run, so the agent
    gets a partial report instead of nothing.
    """
    results, errors = [], []
    for check in CHECKS:
        try:
            result = check.run(ctx)
        except Exception as exc:  # noqa: BLE001 - any crash is reported, never hidden
            errors.append("%s crashed: %s: %s" % (check.ID, type(exc).__name__, exc))
            continue
        if result.id != check.ID:
            errors.append("%s returned a result with id %s" % (check.ID, result.id))
            continue
        results.append(result)
    return results, errors
