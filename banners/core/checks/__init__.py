"""Banner and kit checks: one module per check, the same contract as validate/maya/checks.

Each module defines ID, SEVERITY, FIXABLE, DESCRIPTION and run(ctx) ->
CheckResult. Kit checks run before rendering (is the game's input usable?),
banner checks after (is the rendered banner shippable?). The contract is
checked at import time.

Pure Python 3.9.
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

from banners.core.checks import kit_colours, kit_files, kit_images, kit_licences, kit_slots
from validate.core.result import CheckResult

KIT_CHECKS = (kit_slots, kit_files, kit_licences, kit_colours, kit_images)

_REQUIRED = ("ID", "SEVERITY", "FIXABLE", "DESCRIPTION", "run")


def _validate(checks) -> None:
    seen = set()
    for module in checks:
        missing = [name for name in _REQUIRED if not hasattr(module, name)]
        if missing:
            raise TypeError("%s is missing %s" % (module.__name__, missing))
        if module.ID in seen:
            raise ValueError("duplicate check id %s" % module.ID)
        seen.add(module.ID)


_validate(KIT_CHECKS)


def run_all(checks: Sequence, ctx) -> Tuple[List[CheckResult], List[str]]:
    """(results, tool errors). A crashing check is a tool bug: recorded, and the others still run."""
    results, errors = [], []
    for check in checks:
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
