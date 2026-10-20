"""banner.file_budget: under the size networks recommend (Unity: 10 MB). A warning.

Above it the file still uploads, but the network serves a smaller re-encode
on slow connections and the first frames load later: a cost on users with
cheap phones and data plans.
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity

MB = 1e6  # networks write "MB": read as decimal, the stricter reading

ID = "banner.file_budget"
SEVERITY = Severity.WARNING
FIXABLE = False
DESCRIPTION = "File size at most the placement's recommended target_bytes."


def run(ctx) -> CheckResult:
    size, target = int(ctx.facts.get("bytes") or 0), ctx.placement.target_bytes
    metrics = {"bytes": size, "target_bytes": target}
    if size > target:
        return CheckResult(ID, False, SEVERITY, "%.1f MB, above the recommended %.0f MB" % (size / MB, target / MB),
                           FIXABLE, metrics, offenders=["%d bytes" % size])
    return CheckResult(ID, True, SEVERITY, "%.2f MB (target %.0f MB)" % (size / MB, target / MB), FIXABLE, metrics)
