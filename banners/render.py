"""Run the pinned renderer tools: Chromium frames, the x264 encode, ffprobe, decoding.

Every call goes through banners.tools.require() first, so a missing or
swapped exe is a ToolError (exit 2) before anything is written. The pure
halves (argument lists, parsing) are in banners.core.video.

Standard library only, Python 3.9 (mayapy).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from banners import tools
from banners.core import ToolError
from banners.core import video
from banners.core.rules import BannerRules

RENDER_SCRIPT = tools.RENDER_DIR / "render.mjs"
# Measured: ~0.35 s per 1080x1920 frame (docs/banners.md); generous for a busy machine.
SECONDS_PER_FRAME = 3.0
STARTUP_S = 60.0


def _run(argv: Sequence[str], what: str, timeout: float, cwd: Optional[Path] = None) -> str:
    try:
        proc = subprocess.run(list(argv), cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ToolError("%s timed out after %d s" % (what, timeout))
    except OSError as exc:
        raise ToolError("%s could not start: %s" % (what, exc))
    if proc.returncode != 0:
        raise ToolError("%s failed (exit %d): %s" % (what, proc.returncode, proc.stderr.strip()[-2000:]))
    return proc.stdout


def frames(rules: BannerRules, *, files: Mapping[str, Path], page: str, width: int, height: int,
           frame_list: Sequence[int], out_dir: Path, hide_text_frames: Sequence[int] = (),
           locale: str = "en-US", transparent: bool = False) -> Dict[str, Any]:
    """Capture frames to out_dir/f%05d.png (and bg%05d.png); return the layout record."""
    paths = tools.require(rules)
    out_dir.mkdir(parents=True, exist_ok=True)
    layout_path = out_dir / "layout.json"
    job = {"chromium": paths["chrome-headless-shell"], "width": width, "height": height, "page": page,
           "files": {k: str(Path(v).resolve()) for k, v in sorted(files.items())},
           "frames": list(frame_list), "out_dir": str(out_dir.resolve()),
           "hide_text_frames": sorted(set(hide_text_frames)), "layout": str(layout_path.resolve()),
           "locale": locale, "transparent": transparent}
    job_path = out_dir / "job.json"
    job_path.write_text(json.dumps(job, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    _run([paths["node"], str(RENDER_SCRIPT), str(job_path.resolve())], "the Chromium renderer",
         STARTUP_S + SECONDS_PER_FRAME * (len(frame_list) + len(job["hide_text_frames"])), cwd=tools.RENDER_DIR)
    try:
        record = json.loads(layout_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError("the renderer wrote no readable layout record: %s" % exc)
    if record.get("refused"):
        raise ToolError("the page asked for files the job doesn't provide: %s" % ", ".join(record["refused"]))
    return record


def encode(rules: BannerRules, enc: video.EncodeRules, frames_dir: Path, fps: int, out: Path,
           pix_fmt: str = "yuv420p") -> None:
    paths = tools.require(rules)
    out.parent.mkdir(parents=True, exist_ok=True)
    count = len(list(frames_dir.glob("f*.png")))
    _run(video.encode_args(paths["ffmpeg"], str(frames_dir / "f%05d.png"), fps, str(out), enc, pix_fmt),
         "ffmpeg (encode)", STARTUP_S + count * 1.0)


def probe(rules: BannerRules, path: Path) -> Dict[str, Any]:
    paths = tools.require(rules)
    try:
        detail = json.loads(_run(video.probe_args(paths["ffprobe"], str(path)), "ffprobe", STARTUP_S))
        streams = json.loads(_run(video.streams_args(paths["ffprobe"], str(path)), "ffprobe", STARTUP_S))
    except ValueError as exc:
        raise ToolError("ffprobe printed no JSON: %s" % exc)
    return video.facts(detail, streams, path.stat().st_size)


def decode(rules: BannerRules, path: Path, frame_list: Sequence[int], out_dir: Path) -> Dict[int, Path]:
    """{frame index: decoded RGB PNG}. Checks read these, never the encoder's input."""
    paths = tools.require(rules)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("d*.png"):
        old.unlink()
    wanted = sorted(set(frame_list))
    _run(video.decode_args(paths["ffmpeg"], str(path), wanted, str(out_dir / "d%05d.png")),
         "ffmpeg (decode)", STARTUP_S + 2.0 * len(wanted))
    got = {n: out_dir / ("d%05d.png" % i) for i, n in enumerate(wanted)}
    missing = [n for n, p in got.items() if not p.is_file()]
    if missing:
        raise ToolError("the video has no frame(s) %s" % missing)
    return got


def contact_sheet(rules: BannerRules, images: List[Path], out: Path, columns: int, tile_width: int) -> None:
    """Images side by side (scaled to tile_width), via ffmpeg's tile filter: no Pillow under mayapy."""
    paths = tools.require(rules)
    argv = [paths["ffmpeg"], "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    for img in images:
        argv += ["-i", str(img)]
    rows = (len(images) + columns - 1) // columns
    scaled = "".join("[%d:v]scale=%d:-2,format=rgb24[s%d];" % (i, tile_width, i) for i in range(len(images)))
    pads = "".join("[s%d]" % i for i in range(len(images)))
    graph = scaled + "%sconcat=n=%d:v=1:a=0,tile=%dx%d:padding=8:margin=8:color=white" % (
        pads, len(images), columns, rows)
    argv += ["-filter_complex", graph, "-frames:v", "1"] + video.BITEXACT + [str(out)]
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(argv, "ffmpeg (contact sheet)", STARTUP_S)
