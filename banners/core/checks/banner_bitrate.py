"""banner.bitrate: at least the placement's floor (TikTok: 516 kbps), measured from the file.

File size x 8 / duration, container included: the floor applies to the
upload, which is the whole file. Probed: a static frame in CRF mode came out
at 150 kbps, which is why the encoder runs constant bitrate.
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity

ID = "banner.bitrate"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "Average bitrate at least min_bitrate_kbps of the placement."


def run(ctx) -> CheckResult:
    rate, floor = float(ctx.facts.get("bitrate_kbps") or 0.0), ctx.placement.min_bitrate_kbps
    metrics = {"bitrate_kbps": rate, "min_bitrate_kbps": floor}
    if rate < floor:
        return CheckResult(ID, False, SEVERITY, "%.0f kbps, under the %d kbps floor" % (rate, floor), FIXABLE,
                           metrics, offenders=["%.0f kbps" % rate])
    return CheckResult(ID, True, SEVERITY, "%.0f kbps (floor %d)" % (rate, floor), FIXABLE, metrics)
