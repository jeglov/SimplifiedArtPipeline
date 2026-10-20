"""banners.core.rules: conventions/banners.yaml as typed rules. Pure Python, <1 s."""
from __future__ import annotations

import copy

import pytest

from banners.core import rules as br
from validate.core.conventions import DEFAULT_DIR, ConventionError, _read_yaml

RAW = _read_yaml(DEFAULT_DIR / br.FILE)


def edited(path, value):
    """A deep copy of banners.yaml with one key set (value None deletes it)."""
    data = copy.deepcopy(RAW)
    node = data
    for key in path[:-1]:
        node = node[key]
    if value is None:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return data


P = ("placements", "ua_video_9x16")


def test_shipped_file_loads():
    rules = br.load()
    p = rules.placement("ua_video_9x16")
    assert (p.width, p.height, p.fps, p.codec, p.pix_fmt, p.audio) == (1080, 1920, 30, "h264", "yuv420p", False)
    assert rules.ffmpeg.exe_sha256.keys() == {"ffmpeg", "ffprobe"}
    assert rules.chromium.exe_sha256.keys() == {"chrome-headless-shell"}
    assert rules.playwright.version == "1.63.0"


def test_meta_safe_zone_in_pixels():
    # Meta: 14 % top, 35 % bottom, 6 % each side of 1080x1920.
    zone = br.load().placement("ua_video_9x16").safe_zone
    assert zone.box(1080, 1920) == (65, 269, 1015, 1248)


def test_playwright_pin_matches_package_json():
    import json
    from banners.tools import RENDER_DIR
    pkg = json.loads((RENDER_DIR / "package.json").read_text(encoding="utf-8"))
    assert pkg["dependencies"]["playwright"] == br.load().playwright.version
    lock = json.loads((RENDER_DIR / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["packages"]["node_modules/playwright"]["version"] == br.load().playwright.version


def test_unknown_placement_lists_the_options():
    with pytest.raises(ConventionError, match="ua_video_9x16"):
        br.load().placement("mrec")


@pytest.mark.parametrize("path, value, match", [
    (P + ("codec",), "hevc", "codec"),
    (P + ("width",), 1081, "even width"),
    (P + ("width",), 0, "> 0"),
    (P + ("fps",), True, "bool"),
    (P + ("target_bytes",), 104857601, "target_bytes above max_bytes"),
    (P + ("duration_s",), {"min": 61.0, "max": 60.0}, "min 61.0 > max"),
    (P + ("safe_zone",), {"top": 0.5, "bottom": 0.1, "left": 0.0, "right": 0.0}, r"\[0, 0.5\)"),
    (P + ("safe_zone",), {"top": 0.49, "bottom": 0.49, "left": 0.49, "right": 0.49}, None),
    (P + ("safe_zone",), {"top": 0.6, "bottom": 0.45, "left": 0.0, "right": 0.0}, "margin"),
    (P + ("colour",), "red", "unknown key"),
    (P + ("min_bitrate_kbps",), None, "missing required key"),
    (("tools", "ffmpeg", "archive_sha256"), "ABC", "sha256"),
    (("tools", "ffmpeg", "source"), "http://example.com/x.zip", "https"),
    (("tools", "ffmpeg", "archive_dir"), "../bin", "relative folder"),
    (("tools", "ffmpeg", "version"), "latest", "exact dotted version"),
    (("tools", "ffmpeg", "exe_sha256"), {"ffmpeg": "0" * 64}, "ffprobe: missing"),
    (("tools", "playwright", "version"), "^1.63.0", "exact"),
    (("encode", "bitrate_kbps"), 515, "every encode would fail"),   # boundary: 1 kbps under TikTok's floor
    (("encode", "threads"), 0, "> 0"),                              # 0 = one per core: machine-dependent bytes
    (("encode", "preset"), "placebo2", "preset"),
    (("placements",), {}, "at least one placement"),
    (("placements",), {"UA-9x16": RAW["placements"]["ua_video_9x16"]}, "snake_case"),
])
def test_mistakes_fail_at_load(path, value, match):
    data = edited(path, value)
    if match is None:  # boundary: margins just under the limit still leave a 2 % frame
        assert br.parse(data).placement("ua_video_9x16").safe_zone.top == 0.49
        return
    with pytest.raises(ConventionError, match=match):
        br.parse(data)
