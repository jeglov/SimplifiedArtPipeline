"""banner.format: H.264 in MP4, the placement's pixel format, no audio stream unless asked for.

Every network re-encodes, but first it rejects what it can't decode. A
silent creative with an empty audio track is flagged by some ad reviews as
"audio missing" rather than "silent". The facts come from the file
(ffprobe), not from the encoder settings.
"""
from __future__ import annotations

from validate.core.result import CheckResult, Severity

ID = "banner.format"
SEVERITY = Severity.ERROR
FIXABLE = False
DESCRIPTION = "Container, codec, pixel format and audio streams match the placement."


def run(ctx) -> CheckResult:
    p, f = ctx.placement, ctx.facts
    problems = []
    if p.container not in (f.get("format") or "").split(","):
        problems.append("container %s, expected %s" % (f.get("format"), p.container))
    if f.get("codec") != p.codec:
        problems.append("codec %s, expected %s" % (f.get("codec"), p.codec))
    if f.get("pix_fmt") != p.pix_fmt:
        problems.append("pixel format %s, expected %s" % (f.get("pix_fmt"), p.pix_fmt))
    if not p.audio and f.get("audio_streams"):
        problems.append("%d audio stream(s) in a silent placement" % f["audio_streams"])
    metrics = {"format": f.get("format"), "codec": f.get("codec"), "profile": f.get("profile"),
               "pix_fmt": f.get("pix_fmt"), "audio_streams": f.get("audio_streams")}
    if problems:
        return CheckResult(ID, False, SEVERITY, "; ".join(problems), FIXABLE, metrics, offenders=problems)
    return CheckResult(ID, True, SEVERITY, "%s %s %s, no audio" % (p.container, f.get("codec"), f.get("pix_fmt")),
                       FIXABLE, metrics)
