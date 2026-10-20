"""banner.file_size: under the placement's hard upload limit (strictest network: Unity, 100 MB)."""
from __future__ import annotations

from validate.core.result import CheckResult, Severity

MB = 1e6  # networks write "MB": read as decimal, the stricter reading

ID = "banner.file_size"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "File size at most max_bytes of the placement."


def run(ctx) -> CheckResult:
    size, limit = int(ctx.facts.get("bytes") or 0), ctx.placement.max_bytes
    metrics = {"bytes": size, "max_bytes": limit}
    if size > limit:
        return CheckResult(ID, False, SEVERITY, "%.1f MB, the upload limit is %.1f MB" % (size / MB, limit / MB),
                           FIXABLE, metrics, offenders=["%d bytes" % size])
    return CheckResult(ID, True, SEVERITY, "%.2f MB (limit %.0f MB)" % (size / MB, limit / MB), FIXABLE, metrics)
