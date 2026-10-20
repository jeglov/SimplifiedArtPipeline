"""banners.core.page: template + kit -> the page render.mjs captures. Pure Python, <1 s."""
from __future__ import annotations

import json
import re

import pytest

from banners.core import kit as km
from banners.core import page as pm
from banners.core import rules as br
from banners.core import template as tm

RULES = br.load()
P = RULES.placement("ua_video_9x16")
T = tm.load("hero_endcard")
KIT = km.load("spotcap_grove")
FONTS = km.load_fonts()


def states(page):
    return json.loads(re.search(r'<script type="application/json" id="states">(.*?)</script>', page.html).group(1))


def test_fitted_contain_and_cover():
    assert pm.fitted("contain", 200, 100, 50, 50) == (50.0, 0.0, 100.0, 100.0)    # letterboxed, centred
    assert pm.fitted("cover", 200, 100, 50, 50) == (0.0, -50.0, 200.0, 200.0)     # fills, overflow clipped
    assert pm.fitted("contain", 100, 100, 100, 100) == (0.0, 0.0, 100.0, 100.0)   # boundary: exact fit


def test_page_has_every_layer_and_one_state_per_frame():
    page = pm.build(T, KIT, FONTS, P, "en")
    s = states(page)
    assert s["ids"] == [layer.id for layer in T.layers] and len(s["frames"]) == 240 == page.frames
    assert s["frames"][0][s["ids"].index("hero")][1] == 0.9          # hero scale at frame 0
    assert s["frames"][0][s["ids"].index("cta")][0] == 0.0           # CTA not yet visible
    assert s["frames"][239][s["ids"].index("cta")][0] == 1.0
    assert page.text_layers == ("headline", "subline", "cta")


def test_served_files_are_relative_and_cover_every_asset():
    page = pm.build(T, KIT, FONTS, P, "en")
    assert sorted(page.files) == ["fonts/body.ttf", "fonts/display.ttf", "images/background.png",
                                  "images/hero.png", "images/logo.png"]
    for served in re.findall(r'(?:src|url)\([\'"]?([^\'")]+)', page.html) + re.findall(r'src="([^"]+)"', page.html):
        assert served in page.files, served
    assert str(km.KIT_DIR) not in page.html and ":\\" not in page.html


def test_strings_are_escaped_not_injected():
    kit = km.Kit(KIT.id, KIT.name, True, KIT.path, KIT.palette, KIT.colours, KIT.fonts, KIT.images,
                 {"en": dict(KIT.strings["en"], headline="<img src=x onerror=alert(1)> & </script>")})
    page = pm.build(T, kit, FONTS, P, "en")
    assert "<img src=x" not in page.html and "&lt;img src=x onerror=alert(1)&gt; &amp; &lt;/script&gt;" in page.html


def test_overrides_change_font_size_and_colour_only_within_the_palette():
    base = pm.build(T, KIT, FONTS, P, "en")
    assert base.font_px["headline"] == pytest.approx(0.056 * 1920, abs=0.005)
    o = pm.Overrides(font_scale={"headline": 0.8}, colours={"cta": "cap_red"})
    page = pm.build(T, KIT, FONTS, P, "en", o)
    assert page.font_px["headline"] == pytest.approx(0.056 * 1920 * 0.8, abs=0.005)   # rounded to 0.01 px
    assert page.colours["cta"] == KIT.palette["cap_red"] and "background:%s" % KIT.palette["cap_red"] in page.html
    with pytest.raises(KeyError):
        pm.build(T, KIT, FONTS, P, "en", pm.Overrides(colours={"cta": "#123456"}))   # not a palette name


def test_button_text_area_is_inset_by_half_its_height():
    page = pm.build(T, KIT, FONTS, P, "en")
    h = 0.08 * 1920
    assert re.search(r'id="cta".*?<div class="area" style="left:%s;right:%s;' % (pm._px(h / 2), pm._px(h / 2)),
                     page.html)


def test_visible_spans_and_key_frames():
    head = T.layer("headline")
    spans = pm.visible_spans(head, T.duration_s)
    assert len(spans) == 1 and 0.5 < spans[0][0] < 0.6 and 4.8 < spans[0][1] < 4.9
    assert pm.visible_spans(T.layer("hero"), 8.0) == [(0.0, 8.0)]     # no opacity track: always visible
    keys = pm.key_frames(T, 30, 240)
    assert keys[0] == 0 and keys[-1] == 239 and 90 in keys            # 90: the middle of the subline's time
    assert pm.frame_for(8.0, 30, 240) == 239                          # boundary: the end clamps to the last frame
