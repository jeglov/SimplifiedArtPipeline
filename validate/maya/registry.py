"""The ordered lists of checks and fixes: the full set of tools an agent can call.

Explicit lists, not auto-discovery: adding a check or a fix is a deliberate
one-line change that shows up in code review.
Check order = report order: cheap/fundamental checks first.
"""
from __future__ import annotations

import dataclasses
from typing import Callable, List, Tuple

from validate.core.result import CheckResult, FixResult
from validate.maya.checks import (
    budget_lods,
    budget_materials,
    budget_tris,
    naming_convention,
    scene_has_meshes,
    scene_history,
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
from validate.maya.fixes import scene_history as fix_scene_history
from validate.maya.fixes import topology_lamina_faces as fix_topology_lamina_faces

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
    # scene hygiene
    naming_convention,
    scene_history,
)

# Fixes, in the order they are APPLIED. Order matters: fixes that delete or
# bake geometry come first, so later fixes (transforms, renames) see the
# final topology.
FIXES = (
    fix_topology_lamina_faces,
    fix_scene_history,
)

_REQUIRED = ("ID", "SEVERITY", "FIXABLE", "DESCRIPTION", "run")
_REQUIRED_FIX = ("ID", "DESCRIPTION", "blocker", "apply")


def _validate(checks, fixes) -> None:
    """Fail at import time if a check or fix module breaks the contract."""
    seen = set()
    for module in checks:
        missing = [name for name in _REQUIRED if not hasattr(module, name)]
        if missing:
            raise TypeError("%s is missing %s" % (module.__name__, missing))
        if module.ID in seen:
            raise ValueError("duplicate check id %s" % module.ID)
        seen.add(module.ID)
    fix_ids = set()
    for module in fixes:
        missing = [name for name in _REQUIRED_FIX if not hasattr(module, name)]
        if missing:
            raise TypeError("%s is missing %s" % (module.__name__, missing))
        if module.ID not in seen:
            raise ValueError("fix %s targets unknown check %s" % (module.__name__, module.ID))
        if module.ID in fix_ids:
            raise ValueError("two fixes for check %s" % module.ID)
        fix_ids.add(module.ID)
    # FIXABLE is the public promise in each check module: keep it honest.
    for module in checks:
        if module.FIXABLE != (module.ID in fix_ids):
            raise ValueError("%s: FIXABLE=%s but a fix is%s registered"
                             % (module.ID, module.FIXABLE, "" if module.ID in fix_ids else " not"))


_validate(CHECKS, FIXES)
_CHECK_BY_ID = {c.ID: c for c in CHECKS}
_FIX_BY_ID = {f.ID: f for f in FIXES}


def unknown_override_ids(conventions) -> List[str]:
    """severity_overrides in mesh_budgets.yaml that name no registered check.

    The conventions loader can only check an id's FORMAT (it never imports
    Maya), so a typo like "topology.ngon" would load fine and silently never
    apply. The CLI calls this once per run and refuses to continue.
    """
    known = {check.ID for check in CHECKS}
    return sorted("%s: %s" % (cls.name, check_id)
                  for cls in conventions.classes
                  for check_id, _ in cls.severity_overrides
                  if check_id not in known)


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
        fix = _FIX_BY_ID.get(check.ID)
        if fix is not None and not result.passed:
            # `fixable` = the fix would really run on THIS failure, not just
            # "a fix exists" (e.g. lamina faces on a skinned mesh: no).
            try:
                result = dataclasses.replace(result, fixable=fix.blocker(ctx, result) is None)
            except Exception as exc:  # noqa: BLE001
                errors.append("%s fix blocker crashed: %s: %s" % (check.ID, type(exc).__name__, exc))
        results.append(result)
    return results, errors


def apply_fixes(make_ctx: Callable[[], object]) -> Tuple[List[FixResult], List[str]]:
    """Apply every fix whose check fails, in FIXES order. Returns (fixes, tool_errors).

    make_ctx() must build a FRESH CheckContext from the current scene: each
    fix re-runs its own check first, because an earlier fix may have
    deleted, baked or renamed what the original report pointed at.
    Skipped fixes are recorded with their reason. A fix that crashes stops
    the loop: the scene is in an unknown state and must not be saved.
    """
    fixes, errors = [], []
    for fix in FIXES:
        ctx = make_ctx()
        result = _CHECK_BY_ID[fix.ID].run(ctx)
        if result.passed:
            continue
        reason = fix.blocker(ctx, result)
        if reason is not None:
            fixes.append(FixResult(fix.ID, applied=False, message="skipped: " + reason))
            continue
        try:
            fixes.append(fix.apply(ctx, result))
        except Exception as exc:  # noqa: BLE001
            errors.append("fix %s crashed: %s: %s" % (fix.ID, type(exc).__name__, exc))
            break
    return fixes, errors
