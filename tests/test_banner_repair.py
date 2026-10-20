"""Banner repair: the pure planning (fast) and the loop on the real renderer (marker render, ~2 min)."""
from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from banners import repair as runner
from banners import tools
from banners.core import ToolError
from banners.core import kit as km
from banners.core import repair as rp
from banners.core import rules as br
from banners.core import template as tm
from banners.core.checks.banner_context import contrast, hex_rgb
from validate.core.image import encode_png

RULES = br.load()
POLICY = RULES.repair


def report(*failed, offenders=None, tool_error=None):
    offenders = offenders or {}
    results = [{"id": c, "passed": False, "severity": "error", "offenders": offenders.get(c, [])} for c in failed]
    results.append({"id": "banner.file_budget", "passed": False, "severity": "warning", "offenders": []})
    return {"results": results, "tool_error": tool_error}


# --- plan -----------------------------------------------------------------------

def test_plan_accepts_when_only_warnings_fail():
    assert rp.plan(report(), POLICY).decision == rp.ACCEPT


def test_plan_hands_over_when_any_failure_has_no_fix():
    d = rp.plan(report("banner.text_fit", "banner.logo", offenders={"banner.text_fit": ["headline: 3 lines"]}),
                POLICY)
    assert d.decision == rp.STOP and d.reason.startswith("banner.logo") and d.actions["banner.text_fit"] == "shrink_text"


def test_safe_zone_rides_on_the_text_fit_only_for_the_same_layers():
    tf = {"banner.text_fit": ["headline: 3 lines (max 2)"], "banner.safe_zone": ["headline: frames 16-145 (130 frames)"]}
    d = rp.plan(report("banner.text_fit", "banner.safe_zone", offenders=tf), POLICY)
    assert d.decision == rp.REPAIR and d.actions == {"banner.text_fit": "shrink_text", "banner.safe_zone": "with_text_fit"}
    # The CTA outside the safe zone is not an overflow consequence: hand over.
    tf["banner.safe_zone"] = ["headline: frames 16-145", "cta: frames 160-239"]
    assert rp.plan(report("banner.text_fit", "banner.safe_zone", offenders=tf), POLICY).decision == rp.STOP
    # Safe zone alone: nothing to ride on.
    assert rp.plan(report("banner.safe_zone", offenders=tf), POLICY).decision == rp.STOP


def test_tool_error_stops():
    assert rp.plan(report(tool_error="ffprobe failed"), POLICY).decision == rp.STOP


# --- fixes ----------------------------------------------------------------------

def test_fit_scale():
    three_lines = {"lines": 3, "width": 929, "height": 339, "area": [929, 230]}
    s = rp.fit_scale(three_lines, 2, 1.0)
    assert s == pytest.approx(min((230 / 339) ** 0.5, (2 / 3) ** 0.5), abs=1e-4)
    long_word = {"lines": 1, "width": 1000, "height": 60, "area": [800, 120]}
    assert rp.fit_scale(long_word, 1, 0.97) == pytest.approx(0.8 * 0.97, abs=1e-4)
    fits = {"lines": 2, "width": 800, "height": 230, "area": [800, 230]}   # boundary: exactly fits
    assert rp.fit_scale(fits, 2, 0.97) == 1.0


PALETTE = {"cream": "#FFF6E0", "night": "#0E2416", "gold": "#FFC93C", "bark": "#4A2A14", "forest": "#1F4D2B"}


def test_best_text_colour_on_a_bright_background_is_the_darkest_and_reaches_the_bar():
    bright = [(250, 240, 210)] * 50
    name, ratio = rp.best_text_colour(bright, PALETTE, "cream", 0.1, 3.0)
    assert name == "night" and ratio >= 3.0
    assert rp.best_text_colour(bright, {"cream": "#FFF6E0", "white": "#FFFFFF"}, "cream", 0.1, 3.0) is None


def test_best_cta_smallest_change_first():
    dark_scene = (14, 36, 22)
    # Gold reads on the dark scene; a dark label on gold reads: nothing to change but the label?
    pal = {"gold": "#FFC93C", "brown": "#7A5A20", "night": "#0E2416"}
    pick = rp.best_cta(dark_scene, pal, "gold", "brown", 4.5, 3.0)       # brown on gold is 4.13:1
    assert (pick["slot"], pick["fill"], pick["label"]) == ("label", "gold", "night")
    # A golden scene: the gold fill must go, and a dark fill needs a light label: both change.
    golden_scene = (255, 205, 70)
    pick = rp.best_cta(golden_scene, PALETTE, "gold", "bark", 4.5, 3.0)
    assert pick["slot"] == "both" and contrast(hex_rgb(PALETTE[pick["fill"]]), golden_scene) >= 3.0
    assert contrast(hex_rgb(PALETTE[pick["label"]]), hex_rgb(PALETTE[pick["fill"]])) >= 4.5
    # Only the fill: a forest scene where gold fails but cream reads under a bark label.
    pick = rp.best_cta((180, 150, 40), PALETTE, "gold", "bark", 4.5, 1.2)
    assert pick["label"] == "bark"
    # Nothing in the palette reads: None, and the runner hands over.
    assert rp.best_cta((128, 128, 128), {"a": "#808080", "b": "#818181"}, "a", "b", 4.5, 3.0) is None


def test_policy_mistakes_fail_at_load():
    import copy
    from validate.core.conventions import DEFAULT_DIR, ConventionError, _read_yaml
    raw = _read_yaml(DEFAULT_DIR / br.FILE)
    for key, value, match in (("actions", {"banner.text_fit": "rewrite"}, "expected one of"),
                              ("actions", {"model.closed": "stop"}, "banner"),
                              ("fit_margin", 0.4, r"\[0.5, 1\]")):
        data = copy.deepcopy(raw)
        data["repair"][key] = value
        with pytest.raises(ConventionError, match=match):
            br.parse(data)


# --- the loop on the real renderer ------------------------------------------------

try:
    tools.require(RULES)
    MISSING = None
except ToolError as exc:
    MISSING = str(exc)
real = [pytest.mark.render, pytest.mark.skipif(MISSING is not None, reason=MISSING or "")]
SHORT = tm.load(str(Path(__file__).parent / "fixtures" / "banners" / "short_endcard.yaml"))
# The 1 s test spot fails duration and end card by construction: the policy must
# not hand those over in these tests, so they are judged as warnings here.
LENIENT = dataclasses.replace(RULES, repair=dataclasses.replace(
    POLICY, actions=dict(POLICY.actions)), placements={k: dataclasses.replace(v, duration_min_s=0.5)
                                                       for k, v in RULES.placements.items()},
    checks=dataclasses.replace(RULES.checks, end_card_min_s=0.3))


def validate_record(folder):
    import jsonschema
    from validate.core.conventions import DEFAULT_DIR
    schema = json.loads((DEFAULT_DIR / "banner_repair.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(json.loads((folder / runner.RECORD).read_text(encoding="utf-8")), schema)


def kit_copy(tmp_path, strings=None, background=None):
    dest = tmp_path / "spotcap_grove"
    shutil.copytree(str(km.KIT_DIR / "spotcap_grove"), str(dest))
    if strings:
        text = (dest / "kit.yaml").read_text(encoding="utf-8")
        for old, new in strings.items():
            text = text.replace(old, new)
        (dest / "kit.yaml").write_text(text, encoding="utf-8")
    if background:
        data = encode_png(216, 384, 3, bytes(background) * (216 * 384))
        (dest / "background.png").write_bytes(data)
        rec = json.loads((dest / "background.png.provenance.json").read_text(encoding="utf-8"))
        rec["output_sha256"] = tools.sha256_file(dest / "background.png")
        (dest / "background.png.provenance.json").write_text(json.dumps(rec), encoding="utf-8")
    return km.load(str(dest))


@pytest.mark.render
@pytest.mark.skipif(MISSING is not None, reason=MISSING or "")
def test_a_long_headline_is_shrunk_until_it_fits(tmp_path):
    kit = kit_copy(tmp_path, {"headline: Merge mushrooms, grow a magic grove!":
                              "headline: Merge magic mushrooms and grow the biggest grove in the forest!"})
    code, rec, folder = runner.run(LENIENT, SHORT, kit, km.load_fonts(), "ua_video_9x16", "en", tmp_path / "out")
    assert code == 0 and rec["decision"] == "accept" and rec["summary"]["renders"] == 2, rec
    first = rec["attempts"][0]
    assert "banner.text_fit" in first["failed"] and first["measure_passes"] >= 1
    scale = rec["attempts"][1]["overrides"]["font_scale"]["headline"]
    assert RULES.checks.min_text_share / SHORT.layer("headline").size <= scale < 1.0
    assert json.loads((folder / "banner.report.json").read_text(encoding="utf-8"))["status"] == "pass"
    validate_record(folder)


@pytest.mark.render
@pytest.mark.skipif(MISSING is not None, reason=MISSING or "")
def test_cream_text_on_a_cream_background_is_recoloured_from_the_palette(tmp_path):
    kit = kit_copy(tmp_path, background=(245, 235, 205))
    code, rec, folder = runner.run(LENIENT, SHORT, kit, km.load_fonts(), "ua_video_9x16", "en", tmp_path / "out")
    assert code == 0, rec
    colours = rec["attempts"][-1]["overrides"]["colours"]
    assert colours["text"] in kit.palette and colours["text"] != "cream"
    assert colours["text_shadow"] in kit.palette          # the shadow followed the new text colour
    validate_record(folder)


@pytest.mark.render
@pytest.mark.skipif(MISSING is not None, reason=MISSING or "")
def test_an_unshrinkable_word_is_handed_over(tmp_path):
    kit = kit_copy(tmp_path, {"cta: Play now": "cta: Spielspaßgarantiejetztkostenlosherunterladen"})
    code, rec, folder = runner.run(LENIENT, SHORT, kit, km.load_fonts(), "ua_video_9x16", "en", tmp_path / "out")
    assert code == 1 and rec["decision"] == "stop" and rec["summary"]["renders"] == 1
    validate_record(folder)
    assert "minimum text size" in rec["reason"] and "shorten the string" in rec["reason"]
