"""Result types shared by every check, fix and report.

This module is the contract between the validator and whoever consumes it
(CI, Unity import gate, the Sophia agent). Two design goals drive everything:

1. Machine-readable: plain JSON types only, stable keys, stable check IDs.
2. Deterministic: the same scene must produce a byte-identical report. That
   makes reports diffable in git, cacheable by file hash, and lets an agent
   tell "the asset changed" from "the tool is flaky".

Determinism is enforced here, centrally, instead of trusting each check:
offenders are de-duplicated + sorted (+ capped on output), floats are rounded, and JSON
is written with sorted keys. A check author can't accidentally break it.

Pure Python (no maya import) so reports can be built/parsed anywhere.
Python 3.9 compatible (mayapy 2023): typing.List/Dict, no `X | Y` unions.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Bump when the report shape changes so consumers can branch on it.
# 1.1: "fixes" and "fixed_from" (auto-fix mode), summary.fixes_applied.
SCHEMA_VERSION = "1.1"

# Default max offenders WRITTEN per check. A 50k-tri mesh with every face
# ngon'd would otherwise produce a multi-MB report that is useless to a human
# and wastes an LLM's context window. The full count is still reported in
# offender_count. Results keep every offender in memory; the cap is applied
# when the report is serialised (Report.max_offenders, CLI --max-offenders,
# 0 = unlimited for a fix-planning agent that needs the full list).
OFFENDER_CAP = 50

# Float precision for metrics. 6 decimals = 0.00001 cm, far below anything
# meaningful for game assets, and well above float noise between machines.
FLOAT_DIGITS = 6

# Process exit codes for the CLI. The agent needs to tell apart
# "the ASSET is bad" (1, actionable: fix the mesh) from
# "the TOOL broke" (2, actionable: retry / alert a human).
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_TOOL_ERROR = 2

# Check IDs are "<category>.<name>", lowercase snake case, e.g. "uv.overlap".
# They are public API (agent tool names), so their shape is validated.
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

_DIGITS = re.compile(r"(\d+)")


def _natural_key(name: str) -> List[Any]:
    """Sort key so "|m.e[3]" comes before "|m.e[10]" (plain sort puts e[10] first).

    re.split with a capture group always alternates str, int, str..., so two
    keys never compare an int with a str at the same position.
    """
    return [int(part) if part.isdigit() else part for part in _DIGITS.split(name)]


class Severity(str, Enum):
    """How much a failed check matters.

    Subclassing str means Severity.ERROR == "error" and json.dumps writes
    "error" without a custom encoder.
    """

    ERROR = "error"  # blocks import into Unity
    WARNING = "warning"  # import allowed, surfaced to a human/agent
    INFO = "info"  # metrics only, never fails the asset


class Status(str, Enum):
    """Overall report verdict."""

    PASS = "pass"  # no failed ERROR-severity checks
    FAIL = "fail"  # at least one failed ERROR-severity check
    ERROR = "error"  # the tool itself failed (file missing, load crash...)


def _normalize(value: Any, path: str = "metrics") -> Any:
    """Recursively coerce a metrics value into deterministic JSON types.

    Raises TypeError/ValueError on anything that can't round-trip through
    JSON identically, so a sloppy check fails loudly in tests instead of
    producing a subtly non-deterministic report in production.
    """
    # bool must be tested before int: bool is a subclass of int in Python.
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("%s: NaN/inf is not valid JSON" % path)
        rounded = round(value, FLOAT_DIGITS)
        # round(-0.0000001, 6) == -0.0, which json writes as "-0.0" and
        # makes otherwise-identical reports differ. Collapse to 0.0.
        return 0.0 if rounded == 0 else rounded
    if isinstance(value, dict):
        out = {}
        for key, sub in value.items():
            if not isinstance(key, str):
                raise TypeError("%s: dict keys must be str, got %r" % (path, key))
            out[key] = _normalize(sub, "%s.%s" % (path, key))
        return out
    if isinstance(value, (list, tuple)):
        return [_normalize(v, "%s[%d]" % (path, i)) for i, v in enumerate(value)]
    # Sets (unordered), numpy scalars, MObjects, etc. are rejected on purpose.
    raise TypeError("%s: unsupported metric type %s" % (path, type(value).__name__))


@dataclass(frozen=True)
class CheckResult:
    """Output of one check on one scene. Immutable once built.

    Example JSON:
        {"id": "topology.ngons", "passed": false, "severity": "error",
         "message": "3 ngon faces on 1 mesh", "fixable": true,
         "metrics": {"ngon_count": 3},
         "offenders": ["|crate_GEO.f[12]", ...], "offender_count": 3}
    """

    id: str
    passed: bool
    severity: Severity
    message: str
    fixable: bool
    metrics: Dict[str, Any] = field(default_factory=dict)
    # Node or component names that caused the failure (e.g. "|crate_GEO.f[12]").
    # Accepts any sequence; stored as a sorted, de-duplicated tuple (all of
    # them: capping happens in to_dict()).
    offenders: Sequence[str] = ()
    # Total offenders. Computed, not passed in.
    offender_count: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        # Frozen dataclasses block normal assignment, so normalisation uses
        # object.__setattr__ (the standard idiom for derived fields).
        if not _ID_PATTERN.match(self.id):
            raise ValueError("check id %r must look like 'category.name'" % self.id)

        # Accept "error" as well as Severity.ERROR; reject typos like "err".
        object.__setattr__(self, "severity", Severity(self.severity))

        # Natural order: a cap then keeps the LOWEST component indices,
        # which is what a human expects to see first.
        unique = sorted({str(o) for o in self.offenders}, key=_natural_key)
        if self.passed and unique:
            # A passing check with offenders is a contradiction: it means the
            # check's pass/fail logic and its offender collection disagree.
            raise ValueError("%s: passed=True but offenders were given" % self.id)
        object.__setattr__(self, "offender_count", len(unique))
        object.__setattr__(self, "offenders", tuple(unique))
        object.__setattr__(self, "metrics", _normalize(dict(self.metrics)))

    @property
    def blocking(self) -> bool:
        """True if this result alone makes the asset fail."""
        return not self.passed and self.severity is Severity.ERROR

    def to_dict(self, max_offenders: int = OFFENDER_CAP) -> Dict[str, Any]:
        """JSON-ready dict. max_offenders caps the list (0 = no cap)."""
        offenders = self.offenders[:max_offenders] if max_offenders else self.offenders
        return {
            "id": self.id,
            "passed": self.passed,
            "severity": self.severity.value,
            "message": self.message,
            "fixable": self.fixable,
            "metrics": self.metrics,
            "offenders": list(offenders),
            "offender_count": self.offender_count,
        }


@dataclass(frozen=True)
class FixResult:
    """What one auto-fix did (or why it didn't). Immutable once built.

    Example JSON:
        {"id": "scene.history", "applied": true,
         "message": "baked 1 construction history node on 1 mesh",
         "changed": ["|SM_Crate"], "changed_count": 1}

    `id` is the CHECK id the fix addresses, so an agent can pair it with
    that check's result. applied=False means the fix was skipped on purpose
    (the message says why, e.g. "mesh is skinned") - the asset was not touched.
    """

    id: str
    applied: bool
    message: str
    # Nodes/components the fix modified, e.g. "|SM_Crate.f[3]".
    changed: Sequence[str] = ()
    changed_count: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        if not _ID_PATTERN.match(self.id):
            raise ValueError("fix id %r must be a check id like 'category.name'" % self.id)
        unique = sorted({str(c) for c in self.changed}, key=_natural_key)
        if not self.applied and unique:
            raise ValueError("%s: applied=False but changes were listed" % self.id)
        object.__setattr__(self, "changed_count", len(unique))
        object.__setattr__(self, "changed", tuple(unique))

    def to_dict(self, max_items: int = OFFENDER_CAP) -> Dict[str, Any]:
        changed = self.changed[:max_items] if max_items else self.changed
        return {"id": self.id, "applied": self.applied, "message": self.message,
                "changed": list(changed), "changed_count": self.changed_count}


@dataclass
class Report:
    """Envelope around all check results for one source file.

    `results` keep registry order (cheap checks first, logically grouped);
    they are not re-sorted, so the order itself is part of the contract.

    source.path is whatever the caller passes. The CLI passes a repo-relative
    POSIX path for files inside the repo (validate.core.paths), so the same
    asset gives the same report on every machine. The file's identity is
    still source.sha256.
    """

    source_path: str
    source_sha256: str
    asset_class: str
    results: List[CheckResult] = field(default_factory=list)
    # Set when the tool failed before/while running checks. Results may then
    # be partial or empty; consumers must not treat this as an asset verdict.
    tool_error: Optional[str] = None
    tool: str = "artpipe.validate.maya"
    # Offenders written per check (0 = all). See OFFENDER_CAP.
    max_offenders: int = OFFENDER_CAP
    # Auto-fix mode: what each fix did. `results` are then the re-validation
    # of the FIXED file, and fixed_from is the original {"path", "sha256"}.
    fixes: List[FixResult] = field(default_factory=list)
    fixed_from: Optional[Dict[str, str]] = None

    def __post_init__(self) -> None:
        if self.max_offenders < 0:
            raise ValueError("max_offenders must be >= 0 (0 = unlimited)")
        for kind, items in (("check", self.results), ("fix", self.fixes)):
            seen = set()
            for r in items:
                if r.id in seen:
                    raise ValueError("duplicate %s id in report: %s" % (kind, r.id))
                seen.add(r.id)

    @property
    def status(self) -> Status:
        if self.tool_error is not None:
            return Status.ERROR
        if any(r.blocking for r in self.results):
            return Status.FAIL
        return Status.PASS

    @property
    def exit_code(self) -> int:
        return {
            Status.PASS: EXIT_PASS,
            Status.FAIL: EXIT_FAIL,
            Status.ERROR: EXIT_TOOL_ERROR,
        }[self.status]

    def summary(self) -> Dict[str, int]:
        failed: Tuple[CheckResult, ...] = tuple(r for r in self.results if not r.passed)
        return {
            "total": len(self.results),
            "passed": len(self.results) - len(failed),
            "errors": sum(1 for r in failed if r.severity is Severity.ERROR),
            "warnings": sum(1 for r in failed if r.severity is Severity.WARNING),
            # Failed checks an agent could auto-fix right now.
            "fixable": sum(1 for r in failed if r.fixable),
            "fixes_applied": sum(1 for f in self.fixes if f.applied),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "tool": self.tool,
            "source": {"path": self.source_path, "sha256": self.source_sha256},
            "asset_class": self.asset_class,
            "status": self.status.value,
            "tool_error": self.tool_error,
            "summary": self.summary(),
            "results": [r.to_dict(self.max_offenders) for r in self.results],
            "fixes": [f.to_dict(self.max_offenders) for f in self.fixes],
            "fixed_from": self.fixed_from,
        }

    def to_json(self) -> str:
        """Canonical JSON: sorted keys, 2-space indent, LF, trailing newline.

        No timestamps anywhere in the report, deliberately: a timestamp would
        make every run unique and defeat diffing and hash-based caching.
        """
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
