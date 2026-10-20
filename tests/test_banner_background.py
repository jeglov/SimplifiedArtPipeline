"""kit.background and the background seed retry's stage report. Pure Python on synthetic images, ~1 s."""
from __future__ import annotations

import json
import shutil

import pytest

from banners import art
from banners.core import background as bgm
from banners.core import kit as km
from banners.core import rules as br
from banners.core import template as tm
from banners.core.checks import kit_background
from banners.core.checks.context import KitContext
from validate.core.image import Pixels, encode_png

RULES = br.load()
T = tm.load("hero_endcard")
P = RULES.placement("ua_video_9x16")
RED, GREEN = (225, 50, 40), (40, 110, 50)
WHITE, BLACK = bytes((255, 255, 255)), bytes((0, 0, 0))


def solid(w, h, rgb, alpha=None):
    ch = 4 if alpha is not None else 3
    px = bytes(rgb) + (bytes([alpha]) if alpha is not None else b"")
    return Pixels(w, h, ch, px * (w * h))


def test_coloured_and_dominant_hue():
    assert bgm.coloured((128, 128, 128)) is None            # grey
    assert bgm.coloured((20, 5, 5)) is None                  # too dark to read as a colour
    assert bgm.coloured(RED) == pytest.approx(3.2, abs=0.5)
    assert bgm.dominant_hue(solid(10, 10, RED, 255)) == 10.0  # centre of the 0-20 degree bin
    assert bgm.dominant_hue(solid(10, 10, RED, 0)) is None    # fully transparent: no hero pixels


def test_clash_share_and_hue_wrap():
    hero_box = (100.0, 500.0, 980.0, 1150.0)
    red_bg = bgm.Placed(solid(108, 192, RED), 1080, 1920)
    assert bgm.clash(red_bg, hero_box, 10.0)["same_hue_share"] == 1.0
    assert bgm.clash(red_bg, hero_box, 350.0)["same_hue_share"] == 1.0   # 350 and 3 degrees are 13 apart
    green_bg = bgm.Placed(solid(108, 192, GREEN), 1080, 1920)
    assert bgm.clash(green_bg, hero_box, 10.0)["same_hue_share"] == 0.0


def test_clutter_flat_vs_striped():
    flat = bgm.Placed(solid(108, 192, GREEN), 1080, 1920)
    assert bgm.clutter(flat, [(0, 0, 1080, 400)]) == 0.0
    # Stripes as wide as the sampling step: every horizontal neighbour pair crosses one. Black and
    # white: a red/green edge is invisible to a luminance measure (this red and green are 86 vs 91).
    row = b"".join((WHITE if (x // bgm.STEP) % 2 else BLACK) for x in range(1080))
    striped = bgm.Placed(Pixels(1080, 400, 3, row * 400), 1080, 400)
    # Horizontal steps cross 255, vertical ones 0: mean ~127.5 (a little less at the box edge).
    assert bgm.clutter(striped, [(0, 0, 1080, 400)]) == pytest.approx(127.5, abs=1.5)


def kit_with(tmp_path, background_rgb, hero_rgb=RED):
    dest = tmp_path / "spotcap_grove"
    shutil.copytree(str(km.KIT_DIR / "spotcap_grove"), str(dest))
    for slot, data in (("background", encode_png(108, 192, 3, bytes(background_rgb) * (108 * 192))),
                       ("hero", encode_png(60, 60, 4, (bytes(hero_rgb) + b"\xff") * 3600))):
        (dest / ("%s.png" % slot)).write_bytes(data)
    return dest


def ctx_for(folder):
    return KitContext(T, km.load(str(folder)), km.load_fonts(), RULES, ("en",), ("ua_video_9x16",))


def test_red_hero_on_red_background_warns(tmp_path):
    r = kit_background.run(ctx_for(kit_with(tmp_path, RED)))
    assert not r.passed and r.severity.value == "warning" and "blends into the background" in r.message
    assert r.metrics["ua_video_9x16"]["same_hue_share"] == 1.0


def test_red_hero_on_green_background_passes(tmp_path):
    r = kit_background.run(ctx_for(kit_with(tmp_path, GREEN)))
    assert r.passed and r.metrics["ua_video_9x16"]["same_hue_share"] == 0.0


def test_the_limit_boundary(tmp_path, monkeypatch):
    # Exactly at hero_clash_max passes; one step over warns.
    monkeypatch.setattr(bgm, "clash", lambda bg, box, hue: {"ring_samples": 1000, "same_hue_share": 0.1})
    assert kit_background.run(ctx_for(kit_with(tmp_path, GREEN))).passed
    monkeypatch.setattr(bgm, "clash", lambda bg, box, hue: {"ring_samples": 1000, "same_hue_share": 0.1001})
    assert not kit_background.run(ctx_for(kit_with(tmp_path / "b", GREEN))).passed


def test_the_shipped_kit_passes_and_the_rejected_background_would_not():
    r = kit_background.run(KitContext(T, km.load("spotcap_grove"), km.load_fonts(), RULES, ("en",),
                                      ("ua_video_9x16",)))
    assert r.passed and r.metrics["ua_video_9x16"]["same_hue_share"] < 0.01


def test_stage_report_makes_the_check_a_gate(tmp_path):
    kit = kit_with(tmp_path, GREEN)
    run = tmp_path / "concept_zimage-x"
    run.mkdir()
    (run / "image.png").write_bytes(encode_png(108, 192, 3, bytes(RED) * (108 * 192)))
    report, path = art.background_report(run, kit, "hero_endcard", RULES)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert report.exit_code == 1 and data["results"][0]["severity"] == "error"
    assert data["results"][0]["id"] == "kit.background" and str(tmp_path) not in json.dumps(data)


def test_generation_policy_retries_on_the_background_check():
    from gen.core import rules as gr
    assert gr.load().repair.actions["kit.background"] == "retry"
