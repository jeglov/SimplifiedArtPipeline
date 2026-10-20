"""The batch: N games x placements x locales, reuse by input hash, batch.distinct, the summary.

Pure parts first (<1 s); then a real batch of three kits on the 1 s test template
(marker render, ~1.5 min): the shipped kit, a synthetic second game, and a clone of
the first under another id (it must be flagged as looking alike).
"""
from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

import jsonschema
import pytest

from banners import batch, tools
from banners.core import ToolError
from banners.core import batch as bc
from banners.core import kit as km
from banners.core import rules as br
from validate.core.conventions import DEFAULT_DIR
from validate.core.image import encode_png

RULES = br.load()
SHORT = str(Path(__file__).parent / "fixtures" / "banners" / "short_endcard.yaml")
SCHEMA = json.loads((DEFAULT_DIR / "banner_batch.schema.json").read_text(encoding="utf-8"))


def test_input_hash_changes_with_any_rule():
    base = {"rules": bc.rules_record(RULES, "ua_video_9x16"), "locale": "en"}
    stricter = dataclasses.replace(RULES, checks=dataclasses.replace(RULES.checks, contrast_min=3.1))
    other = {"rules": bc.rules_record(stricter, "ua_video_9x16"), "locale": "en"}
    assert bc.input_hash(base) != bc.input_hash(other)
    assert bc.input_hash(base) == bc.input_hash(json.loads(json.dumps(base)))   # key order doesn't matter


def test_histogram_distance():
    red, green = [(250, 10, 10)] * 100, [(10, 250, 10)] * 100
    assert bc.distance(bc.histogram(red), bc.histogram(red)) == 0.0
    assert bc.distance(bc.histogram(red), bc.histogram(green)) == 1.0
    half = bc.histogram(red[:50] + green[:50])
    assert bc.distance(bc.histogram(red), half) == 0.5
    pairs = bc.distinct_pairs({"a": bc.histogram(red), "b": half, "c": bc.histogram(red)}, 0.5)
    assert pairs == [{"a": "a", "b": "c", "distance": 0.0}]          # boundary: 0.5 is not "< 0.5"


def test_summary_and_exit_code():
    banners = [{"status": "pass", "failed": [], "reused": True, "renders": 1, "seconds": 0.0},
               {"status": "handed_over", "failed": ["banner.logo"], "reused": False, "renders": 1, "seconds": 9.0},
               {"status": "pass", "failed": [], "reused": False, "renders": 2, "seconds": 20.0}]
    kits = [{"status": "pass"}]
    s = bc.summary(banners, kits, {"pairs": []}, 31.0)
    assert (s["passed"], s["handed_over"], s["reused"], s["repaired"], s["render_seconds"]) == (2, 1, 1, 1, 29.0)
    assert s["failures_by_check"] == {"banner.logo": 1}
    assert bc.exit_code(banners, kits) == 1 and bc.exit_code(banners[:1], kits) == 0
    assert bc.exit_code(banners + [{"status": "error"}], kits) == 2


def test_code_hash_covers_the_renderer_script():
    assert len(batch.code_hash()) == 64


# --- a real batch ------------------------------------------------------------------

try:
    tools.require(RULES)
    MISSING = None
except ToolError as exc:
    MISSING = str(exc)

LENIENT = dataclasses.replace(
    RULES, placements={k: dataclasses.replace(v, duration_min_s=0.5) for k, v in RULES.placements.items()},
    checks=dataclasses.replace(RULES.checks, end_card_min_s=0.3))


def clone_kit(root: Path, new_id: str, palette=None, background=None) -> str:
    dest = root / new_id
    shutil.copytree(str(km.KIT_DIR / "spotcap_grove"), str(dest))
    text = (dest / "kit.yaml").read_text(encoding="utf-8").replace("id: spotcap_grove", "id: %s" % new_id)
    for old, new in (palette or {}).items():
        text = text.replace(old, new)
    (dest / "kit.yaml").write_text(text, encoding="utf-8")
    if background:
        (dest / "background.png").write_bytes(encode_png(216, 384, 3, bytes(background) * (216 * 384)))
        rec = json.loads((dest / "background.png.provenance.json").read_text(encoding="utf-8"))
        rec["output_sha256"] = tools.sha256_file(dest / "background.png")
        (dest / "background.png.provenance.json").write_text(json.dumps(rec), encoding="utf-8")
    return str(dest)


@pytest.fixture(scope="module")
def batch_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("kits")
    kits = [str(km.KIT_DIR / "spotcap_grove"),
            clone_kit(root, "tidepool_dash", {'cream: "#FFF6E0"': 'cream: "#FFFFFF"', 'gold: "#FFC93C"': 'gold: "#FF5FA2"'},
                      background=(20, 60, 140)),
            clone_kit(root, "spotcap_clone")]
    out = tmp_path_factory.mktemp("out")
    code, rec, path = batch.run(LENIENT, SHORT, kits, ["ua_video_9x16"], ["en"], out)
    return kits, out, code, rec, path


real = pytest.mark.skipif(MISSING is not None, reason=MISSING or "")


@pytest.mark.render
@real
def test_batch_renders_every_game_and_writes_the_summary(batch_run):
    kits, out, code, rec, path = batch_run
    jsonschema.validate(rec, SCHEMA)
    assert [b["kit"] for b in rec["banners"]] == ["spotcap_grove", "tidepool_dash", "spotcap_clone"]
    assert rec["summary"]["banners"] == 3 and rec["summary"]["passed"] == 3, rec["banners"]
    assert all(not b["reused"] for b in rec["banners"]) and rec["sheet"]
    assert (out / "batch" / "short_endcard.sheet.png").is_file()


@pytest.mark.render
@real
def test_the_clone_is_flagged_as_looking_alike_and_the_other_game_is_not(batch_run):
    _, _, code, rec, _ = batch_run
    d = rec["distinct"]
    assert d["pairs_compared"] == 3 and not d["passed"]
    assert [(p["a"], p["b"]) for p in d["pairs"]] == [("spotcap_clone", "spotcap_grove")]
    assert d["pairs"][0]["distance"] == 0.0
    assert code == 0       # batch.distinct is a warning: the banners themselves pass


@pytest.mark.render
@real
def test_a_second_run_reuses_everything_and_a_change_rerenders_only_that_game(batch_run):
    kits, out, _, _, _ = batch_run
    _, rec, _ = batch.run(LENIENT, SHORT, kits, ["ua_video_9x16"], ["en"], out)[0:3]
    assert rec["summary"]["reused"] == 3 and rec["summary"]["render_seconds"] == 0
    # A new CTA string for one game: only that banner is rendered again.
    kit_yaml = Path(kits[1]) / "kit.yaml"
    kit_yaml.write_text(kit_yaml.read_text(encoding="utf-8").replace("cta: Play now", "cta: Dive in"), encoding="utf-8")
    _, rec, _ = batch.run(LENIENT, SHORT, kits, ["ua_video_9x16"], ["en"], out)[0:3]
    assert [b["reused"] for b in rec["banners"]] == [True, False, True]
    # A stricter rule invalidates every banner (a cached verdict under an old rule is a wrong verdict).
    stricter = dataclasses.replace(LENIENT, checks=dataclasses.replace(LENIENT.checks, contrast_min=3.01))
    hashes = {b["kit"]: b["hash"] for b in rec["banners"]}
    _, rec2, _ = batch.run(stricter, SHORT, kits[:1], ["ua_video_9x16"], ["en"], out)[0:3]
    assert rec2["banners"][0]["hash"] != hashes["spotcap_grove"] and not rec2["banners"][0]["reused"]


@pytest.mark.render
@real
def test_a_failing_kit_skips_its_banners(tmp_path):
    broken = clone_kit(tmp_path, "broken_game")
    (Path(broken) / "logo.png").write_bytes(b"not the logo")       # sha256 no longer matches its record
    code, rec, _ = batch.run(LENIENT, SHORT, [broken], ["ua_video_9x16"], ["en"], tmp_path / "out")
    assert code == 1 and rec["kits"][0]["failed"] == ["kit.files"]
    assert rec["banners"][0]["status"] == "skipped" and "kit.files" in rec["banners"][0]["message"]
