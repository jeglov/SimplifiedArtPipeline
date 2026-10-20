"""ffmpeg and ffprobe as pure functions: the argument lists and the parsed facts.

The encoder settings come from conventions/banners.yaml `encode`. Two
probed facts shape them (docs/banners.md):

* x264 is byte-deterministic for a FIXED thread count, and `-threads 0`
  (one per core) gives other bytes than `-threads 1`. So the count is pinned:
  a banner encoded on an 8-core and a 16-core machine must be the same file.
* A static frame in quality mode (CRF 20) came out at 150 kbps, under
  TikTok's 516 kbps floor. So the encoder runs constant bitrate (CBR with
  HRD filler): the floor holds whatever the content.

Pure Python 3.9: tested without ffmpeg; banners/render.py runs the commands.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Dict, List, Mapping, Optional

from banners.core import ToolError


@dataclass(frozen=True)
class EncodeRules:
    preset: str
    threads: int
    bitrate_kbps: int     # CBR: target = min = max
    bufsize_kbps: int
    keyint_s: float       # a keyframe every N seconds


# Container-level flags that keep the bytes the same on every run: no
# encoder version string, no creation time, no metadata from the inputs.
BITEXACT = ["-fflags", "+bitexact", "-flags:v", "+bitexact", "-map_metadata", "-1"]


def encode_args(ffmpeg: str, pattern: str, fps: int, out: str, enc: EncodeRules, pix_fmt: str = "yuv420p",
                start_number: int = 0) -> List[str]:
    """PNG sequence (pattern like .../f%05d.png) -> H.264 MP4, CBR, no audio."""
    rate = "%dk" % enc.bitrate_kbps
    keyint = max(1, int(round(enc.keyint_s * fps)))
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-framerate", str(fps), "-start_number", str(start_number), "-i", pattern,
            "-an", "-c:v", "libx264", "-preset", enc.preset, "-threads", str(enc.threads),
            "-b:v", rate, "-minrate", rate, "-maxrate", rate, "-bufsize", "%dk" % enc.bufsize_kbps,
            "-x264-params", "nal-hrd=cbr:keyint=%d:min-keyint=%d" % (keyint, keyint),
            "-pix_fmt", pix_fmt, "-r", str(fps), "-fps_mode", "cfr",
            "-movflags", "+faststart"] + BITEXACT + [out]


def probe_args(ffprobe: str, path: str) -> List[str]:
    """Everything the checks read, plus every packet's timestamp (constant frame rate is proven, not assumed)."""
    return [ffprobe, "-v", "error", "-show_format", "-show_streams",
            "-show_entries", "packet=pts_time,duration_time,flags", "-select_streams", "v:0",
            "-of", "json", path]


def streams_args(ffprobe: str, path: str) -> List[str]:
    """All streams (audio included): -select_streams in probe_args hides them."""
    return [ffprobe, "-v", "error", "-show_entries", "stream=index,codec_type,codec_name", "-of", "json", path]


def decode_args(ffmpeg: str, path: str, frames: List[int], out_pattern: str) -> List[str]:
    """Decode the given frame indices to RGB PNGs (out_pattern like .../d%05d.png, numbered 0, 1, ...)."""
    select = "+".join("eq(n\\,%d)" % n for n in sorted(set(frames)))
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", path,
            "-vf", "select=%s" % select, "-fps_mode", "passthrough", "-pix_fmt", "rgb24",
            "-start_number", "0", out_pattern]


def _rate(text: Optional[str]) -> Optional[Fraction]:
    try:
        num, den = str(text).split("/")
        return Fraction(int(num), int(den)) if int(den) else None
    except (ValueError, AttributeError):
        return None


def facts(probe: Mapping[str, Any], streams: Mapping[str, Any], size_bytes: int) -> Dict[str, Any]:
    """The checkable facts of an encoded file, from ffprobe JSON. ToolError if there is no video stream."""
    video = [s for s in probe.get("streams") or [] if s.get("codec_type") == "video"]
    if not video:
        raise ToolError("no video stream in the file")
    v = video[0]
    packets = probe.get("packets") or []
    pts = sorted(float(p["pts_time"]) for p in packets if p.get("pts_time") not in (None, "N/A"))
    deltas = [round(b - a, 6) for a, b in zip(pts, pts[1:])]
    fmt = probe.get("format") or {}
    duration = float(fmt.get("duration") or v.get("duration") or 0.0)
    r_rate, avg_rate = _rate(v.get("r_frame_rate")), _rate(v.get("avg_frame_rate"))
    all_streams = streams.get("streams") or []
    return {
        "format": fmt.get("format_name"),
        "codec": v.get("codec_name"),
        "profile": v.get("profile"),
        "pix_fmt": v.get("pix_fmt"),
        "width": v.get("width"),
        "height": v.get("height"),
        "r_frame_rate": float(r_rate) if r_rate else None,
        "avg_frame_rate": float(avg_rate) if avg_rate else None,
        "frames": len(packets),
        "duration_s": round(duration, 6),
        # A constant frame rate has one packet interval; ffprobe prints 6 decimals.
        "frame_intervals_s": sorted(set(deltas)),
        "bytes": size_bytes,
        "bitrate_kbps": round(size_bytes * 8 / duration / 1000.0, 1) if duration else 0.0,
        "audio_streams": sum(1 for s in all_streams if s.get("codec_type") == "audio"),
        "keyframes": sum(1 for p in packets if "K" in str(p.get("flags", ""))),
    }
