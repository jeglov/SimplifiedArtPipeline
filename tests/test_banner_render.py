"""banners.render on the real pinned Chromium and ffmpeg: the determinism the design rests on.

Marker `render`; skipped when `artpipe banners setup` hasn't run. ~15 s.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from banners import render, tools
from banners.core import ToolError
from banners.core import rules as br
from validate.core.image import decode_png

RULES = br.load()
FONTS = Path(__file__).resolve().parents[1] / "banners" / "fonts"

try:
    tools.require(RULES)
    MISSING = None
except ToolError as exc:
    MISSING = str(exc)

pytestmark = [pytest.mark.render, pytest.mark.skipif(MISSING is not None, reason=MISSING or "")]

W, H = 320, 240
PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
@font-face { font-family: "Fredoka"; src: url("fonts/Fredoka.ttf"); font-weight: 300 700; }
html, body { margin: 0; width: %(w)dpx; height: %(h)dpx; overflow: hidden; background: #224; }
#box { position: absolute; left: 0; top: 0; width: 60px; height: 60px; background: linear-gradient(#fc0, #f60); }
#t { position: absolute; left: 10px; top: 150px; font: 700 40px "Fredoka"; color: #fff; }
</style></head><body><div id="box"></div><div id="t">%(text)s</div>%(extra)s
<script>
window.artpipe = {
  ready() { return { text: ["t"] }; },
  apply(n) { document.getElementById("box").style.transform = "translate(" + (n * 20) + "px, 40px)";
             const r = document.getElementById("box").getBoundingClientRect(); return { box: [r.left, r.top] }; },
  hideText(on) { document.getElementById("t").style.visibility = on ? "hidden" : ""; },
};
</script></body></html>"""


def page(tmp_path, text="Tap!", extra=""):
    p = tmp_path / "index.html"
    p.write_text(PAGE % {"w": W, "h": H, "text": text, "extra": extra}, encoding="utf-8")
    return {"index.html": p, "fonts/Fredoka.ttf": FONTS / "fredoka" / "Fredoka.ttf"}


def shot(tmp_path, name, files, frames=(0, 1, 2), hide=(1,)):
    return render.frames(RULES, files=files, page="index.html", width=W, height=H, frame_list=list(frames),
                         out_dir=tmp_path / name, hide_text_frames=hide)


def digests(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.glob("*.png"))}


def test_two_browser_launches_give_identical_frames(tmp_path):
    files = page(tmp_path)
    a, b = shot(tmp_path, "a", files), shot(tmp_path, "b", files)
    assert digests(tmp_path / "a") == digests(tmp_path / "b")
    assert sorted(digests(tmp_path / "a")) == ["bg00001.png", "f00000.png", "f00001.png", "f00002.png"]
    assert a["frames"] == b["frames"] and a["frames"]["2"]["box"] == [40, 40]
    assert a["browser"] == RULES.chromium.version


def test_frames_are_what_the_state_says(tmp_path):
    shot(tmp_path, "a", page(tmp_path))
    f2 = decode_png(tmp_path / "a" / "f00002.png")
    assert f2.rgb(45, 45)[0] > 200 and f2.rgb(5, 45) == (0x22, 0x22, 0x44)   # box moved to x=40
    # Text hidden in the background pass, the rest unchanged.
    f1, bg1 = decode_png(tmp_path / "a" / "f00001.png"), decode_png(tmp_path / "a" / "bg00001.png")
    assert f1.data != bg1.data and bg1.rgb(30, 175) == (0x22, 0x22, 0x44)


def test_the_fonts_that_really_drew_the_text_are_recorded(tmp_path):
    rec = shot(tmp_path, "a", page(tmp_path, text="Tap Привет"))
    used = {f["family"].split()[0]: f for f in rec["fonts"]["t"]}
    assert used["Fredoka"]["custom"] is True and used["Fredoka"]["glyphs"] == 4
    # Fredoka has no Cyrillic: Chromium quietly drew those 6 letters in a system font.
    fallback = [f for f in rec["fonts"]["t"] if not f["custom"]]
    assert sum(f["glyphs"] for f in fallback) == 6


def test_a_request_outside_the_job_is_refused_and_fails(tmp_path):
    files = page(tmp_path, extra='<img src="https://example.com/x.png"><img src="missing.png">')
    with pytest.raises(ToolError, match="missing.png"):
        shot(tmp_path, "a", files)


def test_a_page_without_the_api_is_a_tool_error(tmp_path):
    p = tmp_path / "index.html"
    p.write_text("<html><body>no api</body></html>", encoding="utf-8")
    with pytest.raises(ToolError, match="window.artpipe"):
        shot(tmp_path, "a", {"index.html": p})


def test_encode_is_byte_identical_and_probes_as_constant_rate(tmp_path):
    shot(tmp_path, "a", page(tmp_path), frames=range(30), hide=())
    one, two = tmp_path / "one.mp4", tmp_path / "two.mp4"
    render.encode(RULES, RULES.encode, tmp_path / "a", 30, one)
    render.encode(RULES, RULES.encode, tmp_path / "a", 30, two)
    assert one.read_bytes() == two.read_bytes()
    facts = render.probe(RULES, one)
    assert (facts["codec"], facts["pix_fmt"], facts["width"], facts["height"]) == ("h264", "yuv420p", W, H)
    assert facts["frames"] == 30 and facts["duration_s"] == pytest.approx(1.0)
    assert len(facts["frame_intervals_s"]) <= 2 and facts["audio_streams"] == 0
    # CBR keeps TikTok's floor even for a tiny, mostly static clip.
    assert facts["bitrate_kbps"] >= RULES.placement("ua_video_9x16").min_bitrate_kbps


def test_decoded_frames_match_the_rendered_ones_closely(tmp_path):
    shot(tmp_path, "a", page(tmp_path), frames=range(30), hide=())
    mp4 = tmp_path / "v.mp4"
    render.encode(RULES, RULES.encode, tmp_path / "a", 30, mp4)
    got = render.decode(RULES, mp4, [0, 29], tmp_path / "dec")
    src, dec = decode_png(tmp_path / "a" / "f00029.png"), decode_png(got[29])
    assert (dec.width, dec.height, dec.channels) == (W, H, 3)
    # Lossy, so close, not equal: mean channel error under 4 / 255 on the background.
    err = [abs(a - b) for a, b in zip(src.rgb(300, 20), dec.rgb(300, 20))]
    assert max(err) <= 6
    with pytest.raises(ToolError, match="no frame"):
        render.decode(RULES, mp4, [30], tmp_path / "dec2")   # boundary: one past the last frame


def test_contact_sheet(tmp_path):
    shot(tmp_path, "a", page(tmp_path))
    out = tmp_path / "sheet.png"
    imgs = sorted((tmp_path / "a").glob("f*.png"))
    render.contact_sheet(RULES, imgs, out, columns=3, tile_width=100)
    sheet = decode_png(out)
    assert (sheet.width, sheet.height) == (3 * 100 + 4 * 8, 76 + 2 * 8)
