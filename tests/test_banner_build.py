"""banners.build on the real pinned tools: the shipped kit in a 1 s template (30 frames). Marker render, ~30 s."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from banners import build, render, tools
from banners.core import ToolError
from banners.core import kit as km
from banners.core import rules as br
from banners.core import template as tm
from validate.core.image import decode_png

RULES = br.load()
SHORT = tm.load(str(Path(__file__).parent / "fixtures" / "banners" / "short_endcard.yaml"))
try:
    tools.require(RULES)
    MISSING = None
except ToolError as exc:
    MISSING = str(exc)

pytestmark = [pytest.mark.render, pytest.mark.skipif(MISSING is not None, reason=MISSING or "")]


@pytest.fixture(scope="module")
def banner(tmp_path_factory):
    out = tmp_path_factory.mktemp("banners")
    return build.render_banner(RULES, SHORT, km.load("spotcap_grove"), km.load_fonts(), "ua_video_9x16", "en", out)


def test_outputs_and_what_is_kept(banner):
    names = sorted(p.name for p in banner.folder.iterdir())
    assert names == ["banner.mp4", "decoded", "frames", "layout.json", "page", "sheet.png"]
    keys = banner.layout["page"]["key_frames"]
    kept = sorted(p.name for p in (banner.folder / "frames").iterdir())
    assert kept == sorted(["f%05d.png" % n for n in keys] + ["bg%05d.png" % n for n in keys])  # the rest deleted
    assert sorted(banner.key_frames) == keys and all(p.is_file() for p in banner.key_frames.values())


def test_the_video_is_what_the_placement_asks_for(banner):
    facts = render.probe(RULES, banner.mp4)
    assert (facts["width"], facts["height"], facts["codec"], facts["frames"]) == (1080, 1920, "h264", 30)
    assert facts["audio_streams"] == 0 and facts["bitrate_kbps"] >= 516


def test_layout_record_covers_every_frame_and_layer(banner):
    frames = banner.layout["frames"]
    assert sorted(int(k) for k in frames) == list(range(30))
    assert set(frames["0"]) == {"background", "hero", "headline", "subline", "logo", "cta"}
    assert frames["0"]["cta"]["o"] == 0.0 and frames["29"]["cta"]["o"] == 1.0
    fonts = banner.layout["fonts"]
    assert set(fonts) == {"headline", "subline", "cta"} and all(f["custom"] for v in fonts.values() for f in v)
    text = banner.layout["ready"]["measure"]["text"]
    assert text["cta"]["lines"] == 1 and banner.layout["page"]["font_px"]["cta"] == pytest.approx(84.48)


def test_no_machine_path_is_left_in_the_outputs(banner):
    blob = (banner.folder / "layout.json").read_text(encoding="utf-8") + \
        (banner.folder / "page" / "index.html").read_text(encoding="utf-8")
    assert str(Path.home()) not in blob and str(Path.home()).replace("\\", "/") not in blob
    assert not (banner.folder / "frames" / "job.json").exists()


def test_background_pass_hides_text_and_buttons_only(banner):
    n = 29  # end card: logo (image) and CTA (button) visible
    full, bg = decode_png(banner.folder / "frames" / ("f%05d.png" % n)), banner.background[n]
    bgp = decode_png(bg)
    cta = banner.layout["frames"][str(n)]["cta"]["box"]
    cx, cy = int((cta[0] + cta[2]) / 2), int((cta[1] + cta[3]) / 2) - 30
    assert full.rgb(cx, cy) != bgp.rgb(cx, cy)                      # the gold button is gone
    logo = banner.layout["frames"][str(n)]["logo"]["content"]
    lx, ly = int((logo[0] + logo[2]) / 2), int((logo[1] + logo[3]) / 2)
    assert full.rgb(lx, ly) == bgp.rgb(lx, ly)                      # the logo is not text: it stays


def test_rendering_again_gives_the_same_file(banner, tmp_path):
    again = build.render_banner(RULES, SHORT, km.load("spotcap_grove"), km.load_fonts(), "ua_video_9x16", "en",
                                tmp_path)
    assert again.mp4.read_bytes() == banner.mp4.read_bytes()
    a = json.loads((again.folder / "layout.json").read_text(encoding="utf-8"))
    assert a == banner.layout


def test_the_real_checks_on_the_real_render(banner):
    import jsonschema
    from banners import check
    from validate.core.conventions import DEFAULT_DIR
    report, path = check.check(RULES, banner.folder, SHORT)
    data = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.validate(data, json.loads((DEFAULT_DIR / "report.schema.json").read_text(encoding="utf-8")))
    failed = sorted(r["id"] for r in data["results"] if not r["passed"])
    # A 1 s test spot: too short for the placement, and its end card holds 0.4 s. Everything else passes,
    # pixels included (decoded frame 0, contrast on the text-hidden renders).
    assert failed == ["banner.duration", "banner.end_card"], [r["message"] for r in data["results"] if not r["passed"]]
    assert data["asset_class"] == "ua_video_9x16" and data["tool"] == "artpipe.banners.banner"
    assert data["source"]["path"].endswith("banner.mp4") and data["tool_error"] is None


def test_check_rebuilds_its_context_from_the_folder_alone(banner):
    from banners import check
    ctx = check.context(RULES, banner.folder)       # template found from layout.json
    assert ctx.template.id == "short_endcard" and ctx.placement.id == "ua_video_9x16"
