"""The banner.* checks on a synthetic banner: the real template at a 108x192 placement, a layout
record synthesised from the template's own keyframes, tiny PNGs for the pixels. Pure Python, ~1 s.

Each check is proven to pass on a good banner, fail on the defect it exists for, and sit on the
right side of its boundary.
"""
from __future__ import annotations

import copy
import dataclasses

import pytest

from banners.core import checks
from banners.core import page as pm
from banners.core import rules as br
from banners.core import template as tm
from banners.core import timeline as tl
from banners.core.checks.banner_context import BannerContext, contrast, hex_rgb
from validate.core.image import encode_png

RULES = br.load()
T = tm.load("hero_endcard")
P = dataclasses.replace(RULES.placement("ua_video_9x16"), width=108, height=192)
COLOURS = {"text": "#FFF6E0", "text_shadow": "#0E2416", "cta": "#FFC93C", "cta_text": "#4A2A14"}
DARK = (20, 50, 30)


def layer_rects(layer, st):
    """Box and drawn content of a layer in a frame: the box scaled about its centre, then translated."""
    x, y, w, h = layer.box[0] * P.width, layer.box[1] * P.height, layer.box[2] * P.width, layer.box[3] * P.height
    cx, cy = x + w / 2 + st["x"] * P.width, y + h / 2 + st["y"] * P.height
    s = st["scale"]
    box = [cx - w * s / 2, cy - h * s / 2, cx + w * s / 2, cy + h * s / 2]
    # Text draws a centred block 80 % of the box wide; images fill their box.
    k = 0.8 if layer.kind != "image" else 1.0
    content = [cx - w * s * k / 2, cy - h * s * k / 2, cx + w * s * k / 2, cy + h * s * k / 2]
    return [round(v, 2) for v in box], [round(v, 2) for v in content]


def synthetic_layout(template=T):
    frames = {}
    tracks = {layer.id: layer.tracks for layer in template.layers}
    for n, states in enumerate(tl.states(tracks, template.duration_s, P.fps)):
        frames[str(n)] = {}
        for layer in template.layers:
            box, content = layer_rects(layer, states[layer.id])
            frames[str(n)][layer.id] = {"o": states[layer.id]["opacity"], "box": box, "content": content}
    text = {layer.id: {"lines": 1, "width": 50, "height": 10, "area": [80, 12]}
            for layer in template.layers if layer.kind != "image"}
    images = {layer.id: {"natural": [400, 100], "drawn": [80, 20]} for layer in template.layers
              if layer.kind == "image"}
    fonts = {lid: [{"family": "Fredoka", "postscript": "Fredoka-Bold", "custom": True, "glyphs": 12}] for lid in text}
    font_px = {layer.id: round(layer.size * P.height, 2) for layer in template.layers if layer.kind != "image"}
    return {"frames": frames, "ready": {"measure": {"text": text, "images": images}}, "fonts": fonts,
            "page": {"fps": P.fps, "frames": len(frames), "colours": dict(COLOURS), "font_px": font_px}}


FACTS = {"format": "mov,mp4,m4a,3gp,3g2,mj2", "codec": "h264", "profile": "High", "pix_fmt": "yuv420p",
         "width": 108, "height": 192, "r_frame_rate": 30.0, "avg_frame_rate": 30.0, "frames": 240,
         "duration_s": 8.0, "frame_intervals_s": [0.033333, 0.033334], "bytes": 5_000_000,
         "bitrate_kbps": 5000.0, "audio_streams": 0, "keyframes": 4}


def png(path, colour=DARK, w=108, h=192, noise=False):
    data = bytearray()
    stripes = colour == "stripes"
    for y in range(h):
        for x in range(w):
            if stripes:   # every 5th column bright: 20 % of a 4-px sample grid
                c = (250, 245, 230) if x % 5 == 0 else DARK
            else:
                c = colour if not noise else ((x * 7) % 256, (y * 5) % 256, ((x + y) * 3) % 256)
            data += bytes(c)
    path.write_bytes(encode_png(w, h, 3, bytes(data)))
    return path


@pytest.fixture
def make(tmp_path):
    """make(layout=..., facts=..., bg=colour, first=noise?) -> {check id: result}."""
    def run(layout=None, facts=None, bg=DARK, first_noise=True, template=T):
        layout = layout or synthetic_layout(template)
        keys = pm.key_frames(template, P.fps, len(layout["frames"]))
        bgs = {n: png(tmp_path / ("bg%d.png" % n), bg) for n in keys}
        dec = {0: png(tmp_path / "d0.png", noise=first_noise, colour=DARK if bg == "stripes" else bg)}
        ctx = BannerContext(template, P, RULES, layout, facts or dict(FACTS), dec, bgs)
        res, errors = checks.run_all(checks.BANNER_CHECKS, ctx)
        assert errors == []
        return {r.id: r for r in res}
    return run


def test_a_good_banner_passes_every_check(make):
    res = make()
    assert all(r.passed for r in res.values()), {k: r.message for k, r in res.items() if not r.passed}
    assert len(res) == len(checks.BANNER_CHECKS) == 17


# --- file facts ---------------------------------------------------------------

@pytest.mark.parametrize("cid, change, passes", [
    ("banner.dimensions", {"height": 190}, False),
    ("banner.format", {"codec": "hevc"}, False),
    ("banner.format", {"pix_fmt": "yuv444p"}, False),
    ("banner.format", {"audio_streams": 1}, False),
    ("banner.format", {"format": "matroska,webm"}, False),
    ("banner.duration", {"frames": 239}, False),
    ("banner.duration", {"duration_s": 60.0}, True),             # boundary: exactly the max
    ("banner.duration", {"duration_s": 60.01}, False),
    ("banner.duration", {"duration_s": 5.99}, False),             # under the 6 s house minimum
    ("banner.frame_rate", {"frame_intervals_s": [0.033333, 0.066667]}, False),   # a dropped frame
    ("banner.frame_rate", {"r_frame_rate": 29.97}, False),
    ("banner.frame_rate", {"frame_intervals_s": []}, False),
    ("banner.file_size", {"bytes": 100000000}, True),             # boundary: exactly 100 MB (decimal)
    ("banner.file_size", {"bytes": 100000001}, False),
    ("banner.file_size", {"bytes": 104857600}, False),            # 100 MiB is over a decimal 100 MB
    ("banner.file_budget", {"bytes": 10000001}, False),
    ("banner.bitrate", {"bitrate_kbps": 516.0}, True),            # boundary: exactly TikTok's floor
    ("banner.bitrate", {"bitrate_kbps": 515.9}, False),
])
def test_file_fact_checks(make, cid, change, passes):
    r = make(facts=dict(FACTS, **change))[cid]
    assert r.passed is passes, r.message


def test_file_budget_is_only_a_warning(make):
    res = make(facts=dict(FACTS, bytes=20_000_000))
    assert res["banner.file_budget"].severity.value == "warning" and res["banner.file_size"].passed


# --- layout -------------------------------------------------------------------

def shift(layout, lid, dy, frames=None):
    for n, f in layout["frames"].items():
        if frames is None or int(n) in frames:
            for key in ("box", "content"):
                f[lid][key] = [f[lid][key][0], f[lid][key][1] + dy, f[lid][key][2], f[lid][key][3] + dy]


def test_safe_zone_catches_a_cta_under_the_platform_ui(make):
    layout = synthetic_layout()
    shift(layout, "cta", 60)        # down into the bottom 35 %
    r = make(layout=layout)["banner.safe_zone"]
    assert not r.passed and r.offenders[0].startswith("cta: frames ") and "cta.bottom" in r.metrics["outside_px"]


def test_safe_zone_boundary_and_invisible_frames(make):
    zone = P.safe_zone.box(P.width, P.height)
    layout = synthetic_layout()
    bottom = max(f["cta"]["content"][3] for f in layout["frames"].values() if f["cta"]["o"] >= 0.99)
    shift(layout, "cta", zone[3] - bottom + 0.5)          # touching the edge + the 0.5 px tolerance
    assert make(layout=layout)["banner.safe_zone"].passed
    shift(layout, "cta", 0.01)
    assert not make(layout=copy.deepcopy(layout))["banner.safe_zone"].passed
    # A layer that is outside only while fading in (opacity < 0.99) is not judged.
    layout = synthetic_layout()
    fading = [int(n) for n, f in layout["frames"].items() if 0 < f["cta"]["o"] < 0.99]
    shift(layout, "cta", 80, frames=set(fading))
    assert fading and make(layout=layout)["banner.safe_zone"].passed


def test_text_fit_three_lines_and_overlong_word(make):
    layout = synthetic_layout()
    layout["ready"]["measure"]["text"]["headline"] = {"lines": 3, "width": 80, "height": 18, "area": [80, 12]}
    r = make(layout=layout)["banner.text_fit"]
    assert not r.passed and r.fixable and "headline: 3 lines (max 2)" in r.offenders[0]
    layout = synthetic_layout()
    layout["ready"]["measure"]["text"]["cta"] = {"lines": 1, "width": 81, "height": 10, "area": [80, 12]}
    assert "81 px wide in a 80 px area" in make(layout=layout)["banner.text_fit"].offenders[0]
    layout["ready"]["measure"]["text"]["cta"]["width"] = 80.5        # boundary: within the 0.5 px tolerance
    assert make(layout=layout)["banner.text_fit"].passed


def test_font_fallback(make):
    layout = synthetic_layout()
    layout["fonts"]["subline"].append({"family": "Times New Roman", "postscript": "TNR", "custom": False,
                                       "glyphs": 6})
    r = make(layout=layout)["banner.font_fallback"]
    assert not r.passed and r.offenders == ("subline: 6 glyphs in Times New Roman",)
    layout["fonts"]["cta"] = []
    assert "cta: no font record" in " ".join(make(layout=layout)["banner.font_fallback"].offenders)


def test_text_size_floor(make):
    floor = RULES.checks.min_text_share * P.height
    layout = synthetic_layout()
    layout["page"]["font_px"]["subline"] = floor            # boundary: exactly the floor
    assert make(layout=layout)["banner.text_size"].passed
    layout["page"]["font_px"]["subline"] = floor - 0.1
    r = make(layout=layout)["banner.text_size"]
    assert not r.passed and r.severity.value == "warning"


def test_cta_size(make):
    layout = synthetic_layout()
    layout["page"]["font_px"]["cta"] = RULES.checks.cta_min_text_share * P.height - 0.1
    assert "label" in make(layout=layout)["banner.cta_size"].offenders[0]


def test_end_card_needs_logo_and_cta_to_the_last_frame(make):
    layout = synthetic_layout()
    layout["frames"]["239"]["logo"]["o"] = 0.5
    r = make(layout=layout)["banner.end_card"]
    assert not r.passed and "logo: not fully visible on 1 end-card frame(s), first 239" in r.offenders[0]


def test_end_card_minimum_hold(make):
    t = dataclasses.replace(T, end_card_from_s=6.05)       # holds 1.95 s: under the 2 s house rule
    r = make(template=t)["banner.end_card"]
    assert not r.passed and r.offenders == ("end card holds 1.93 s (min 2.0 s)",)   # from frame 182
    t = dataclasses.replace(T, end_card_from_s=6.0)        # boundary: exactly 2.0 s
    assert make(template=t)["banner.end_card"].passed


def test_logo_stretched_or_cropped(make):
    layout = synthetic_layout()
    layout["ready"]["measure"]["images"]["logo"] = {"natural": [400, 100], "drawn": [84, 20]}   # 5 % wide
    r = make(layout=layout)["banner.logo"]
    assert not r.passed and "stretched" in r.offenders[0]
    layout["ready"]["measure"]["images"]["logo"]["drawn"] = [80.1, 20]   # 0.1 %: inside the tolerance
    assert make(layout=layout)["banner.logo"].passed
    layout = synthetic_layout()
    shift(layout, "logo", -60)
    assert "cropped by the frame edge" in " ".join(make(layout=layout)["banner.logo"].offenders)


# --- pixels -------------------------------------------------------------------

def test_first_frame_flat_or_empty(make):
    r = make(first_noise=False)["banner.first_frame"]
    assert not r.passed and "nearly flat" in r.offenders[0]
    layout = synthetic_layout()
    for lid, st in layout["frames"]["0"].items():
        if lid != "background":
            st["o"] = 0.0
    assert "only the background" in " ".join(make(layout=layout)["banner.first_frame"].offenders)


def test_contrast_cream_text_on_a_bright_background(make):
    r = make(bg=(250, 240, 210))["banner.contrast"]
    assert not r.passed and r.fixable and {o.split(":")[0] for o in r.offenders} == {"headline", "subline"}
    assert r.metrics["layers"]["headline"]["contrast_low"] < 1.2


def test_contrast_boundary_grey(make):
    # The grey where cream #FFF6E0 reaches exactly 3:1, then one step brighter.
    cream = (0xFF, 0xF6, 0xE0)
    greys = [g for g in range(256) if contrast(cream, (g, g, g)) >= 3.0]
    edge = max(greys)
    assert make(bg=(edge, edge, edge))["banner.contrast"].passed
    assert not make(bg=(edge + 1,) * 3)["banner.contrast"].passed


def test_cta_contrast_label_and_scene(make):
    layout = synthetic_layout()
    layout["page"]["colours"]["cta_text"] = "#FFC93C"                 # label = fill
    r = make(layout=layout)["banner.cta_contrast"]
    assert not r.passed and "label on fill 1.00:1" in r.offenders[0]
    r = make(bg=(255, 205, 70))["banner.cta_contrast"]               # a golden scene behind a gold button
    assert not r.passed and "button on the scene" in r.offenders[-1] and r.fixable


def test_contrast_is_judged_on_the_worst_pixels_not_the_median(make, tmp_path):
    # A dark background with every 5th column bright: the median sample is dark (passes),
    # the worst 10 % are bright (fails). The check must fail.
    layout = synthetic_layout()
    res = make(layout=layout, bg="stripes")
    r = res["banner.contrast"]
    assert not r.passed
    m = r.metrics["layers"]["headline"]
    assert m["contrast_median"] >= 3.0 > m["contrast_low"]


@pytest.mark.parametrize("label, passes", [("#7A5A20", False), ("#6E5019", True)])
def test_cta_label_contrast_boundary(make, label, passes):
    # Brown on the gold fill: #7A5A20 is 4.13:1 (under 4.5), #6E5019 is 4.84:1 (over).
    layout = synthetic_layout()
    layout["page"]["colours"]["cta_text"] = label
    ratio = contrast(hex_rgb(label), hex_rgb(COLOURS["cta"]))
    assert (ratio >= 4.5) is passes
    assert make(layout=layout)["banner.cta_contrast"].passed is passes
