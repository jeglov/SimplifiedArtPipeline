"""Game kits and the kit.* checks, on a synthetic kit in a temp folder. Pure Python, <1 s."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from banners.core import checks
from banners.core import kit as km
from banners.core import rules as br
from banners.core import template as tm
from banners.core.checks import kit_images
from banners.core.checks.context import KitContext
from validate.core.conventions import DEFAULT_DIR, ConventionError
from validate.core.image import encode_png

RULES = br.load()
TEMPLATE = tm.load("hero_endcard")
FONTS = km.load_fonts()
SCHEMA = json.loads((DEFAULT_DIR / "banner_provenance.schema.json").read_text(encoding="utf-8"))

KIT = {
    "version": 1,
    "game": {"id": "test_game", "name": "Test Game", "fictional": True},
    "palette": {"white": "#FFFFFF", "black": "#000000", "gold": "#FFC93C", "brown": "#4A2A14"},
    "colours": {"text": "white", "text_shadow": "black", "cta": "gold", "cta_text": "brown"},
    "fonts": {"display": "fredoka", "body": "noto_sans"},
    "images": {"background": {"file": "background.png", "licence": "generated"},
               "hero": {"file": "hero.png", "licence": "generated"},
               "logo": {"file": "logo.png", "licence": "own-work"}},
    "strings": {"en": {"headline": "Merge it!", "subline": "Puzzles", "cta": "Play now"}},
}
# (width, height, channels): big enough not to be upscaled past 1.5x in 1080x1920.
SIZES = {"background": (864, 1536, 3), "hero": (900, 640, 4), "logo": (560, 160, 4)}
MODELS = [{"id": "z_image_turbo_int8", "sha256": "a" * 64, "licence": "Apache-2.0"}]


def png(path: Path, w: int, h: int, ch: int) -> str:
    data = encode_png(w, h, ch, bytes([128]) * (w * h * ch))
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def sidecar(path: Path, slot: str, sha: str, licence: str, models=MODELS):
    record = {"schema_version": "1.0", "tool": "artpipe.banners.art", "slot": slot, "output": path.name,
              "output_sha256": sha, "kind": "generated" if models else "rendered", "licence": licence,
              "models": models, "from": {"run": "out/gen/runs/x"}}
    km.provenance_path(path).write_text(json.dumps(record), encoding="utf-8")
    return record


def make_kit(tmp_path, kit=None, sizes=None, skip_sidecar=()):
    folder = tmp_path / "test_game"
    folder.mkdir(parents=True, exist_ok=True)
    data = kit or KIT
    (folder / "kit.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    for slot, (w, h, ch) in (sizes or SIZES).items():
        sha = png(folder / ("%s.png" % slot), w, h, ch)
        if slot not in skip_sidecar:
            lic = data["images"][slot]["licence"]
            sidecar(folder / ("%s.png" % slot), slot, sha, lic, MODELS if lic == "generated" else [])
    return folder


def ctx(folder, locales=("en",)):
    return KitContext(TEMPLATE, km.load(str(folder)), FONTS, RULES, tuple(locales), ("ua_video_9x16",))


def results(folder, locales=("en",)):
    res, errors = checks.run_all(checks.KIT_CHECKS, ctx(folder, locales))
    assert errors == []
    return {r.id: r for r in res}


def with_change(fn):
    data = json.loads(json.dumps(KIT))
    fn(data)
    return data


def test_a_complete_kit_passes_every_check(tmp_path):
    res = results(make_kit(tmp_path))
    assert all(r.passed for r in res.values()), {k: r.message for k, r in res.items() if not r.passed}
    assert list(res) == ["kit.slots", "kit.files", "kit.licences", "kit.colours", "kit.images", "kit.background"]


def test_the_shipped_kit_passes(tmp_path):
    k = km.load("spotcap_grove")
    res, errors = checks.run_all(checks.KIT_CHECKS, KitContext(TEMPLATE, k, FONTS, RULES, ("en",), ("ua_video_9x16",)))
    assert errors == [] and all(r.passed for r in res), [r.message for r in res if not r.passed]
    assert k.fictional is True


def test_shipped_sidecars_match_the_schema():
    for path in sorted((km.KIT_DIR).glob("*/*.provenance.json")):
        jsonschema.validate(json.loads(path.read_text(encoding="utf-8")), SCHEMA)


def test_whitespace_string_is_an_empty_slot(tmp_path):
    folder = make_kit(tmp_path, with_change(lambda d: d["strings"]["en"].__setitem__("cta", "   ")))
    r = results(folder)["kit.slots"]
    assert not r.passed and r.offenders == ("text.cta [en]",)


def test_a_locale_without_strings_fails(tmp_path):
    r = results(make_kit(tmp_path), locales=("en", "de"))["kit.slots"]
    assert not r.passed and "strings.de" in r.offenders


def test_colour_slot_pointing_nowhere(tmp_path):
    folder = make_kit(tmp_path, with_change(lambda d: d["colours"].__setitem__("cta", "silver")))
    r = results(folder)["kit.slots"]
    assert not r.passed and "palette has no 'silver'" in r.offenders[0]


def test_unknown_font_id(tmp_path):
    folder = make_kit(tmp_path, with_change(lambda d: d["fonts"].__setitem__("body", "comic_sans")))
    assert "no font 'comic_sans'" in results(folder)["kit.slots"].offenders[0]


def test_a_file_changed_after_its_record_fails(tmp_path):
    folder = make_kit(tmp_path)
    (folder / "logo.png").write_bytes((folder / "logo.png").read_bytes() + b"\0")
    r = results(folder)["kit.files"]
    assert not r.passed and r.offenders == ("image.logo: logo.png changed (sha256 differs from the record)",)


def test_an_image_without_a_sidecar_fails_files_and_licences(tmp_path):
    res = results(make_kit(tmp_path, skip_sidecar=("hero",)))
    assert "image.hero: hero.png has no readable provenance record" in res["kit.files"].offenders
    assert not res["kit.licences"].passed


def test_disallowed_licence(tmp_path):
    folder = make_kit(tmp_path, with_change(lambda d: d["images"]["logo"].__setitem__("licence", "CC-BY-NC-4.0")))
    assert "image.logo: licence 'CC-BY-NC-4.0' is not allowed" in results(folder)["kit.licences"].offenders


def test_generated_with_a_model_whose_outputs_we_cannot_ship(tmp_path):
    folder = make_kit(tmp_path)
    sha = hashlib.sha256((folder / "hero.png").read_bytes()).hexdigest()
    sidecar(folder / "hero.png", "hero", sha, "generated",
            [{"id": "dino_v3_vitl", "sha256": "b" * 64, "licence": "DINOv3-License"}])
    r = results(folder)["kit.licences"]
    assert not r.passed and "dino_v3_vitl (DINOv3-License)" in r.offenders[0]


def test_bad_colour_value(tmp_path):
    folder = make_kit(tmp_path, with_change(lambda d: d["palette"].__setitem__("white", "#FFF")))
    r = results(folder)["kit.colours"]
    assert not r.passed and r.offenders == ("white: '#FFF'",)


def test_opaque_hero_is_drawn_as_a_rectangle(tmp_path):
    sizes = dict(SIZES, hero=(900, 640, 3))
    r = results(make_kit(tmp_path, sizes=sizes))["kit.images"]
    assert not r.passed and r.severity.value == "warning" and "no alpha" in r.offenders[0]


def test_upscale_limit_boundary(tmp_path):
    # Background: cover a 1080x1920 frame, animated to scale 1.12. A 1.5x limit
    # needs width >= 1080 x 1.12 / 1.5 = 806.4 px (height follows at 16:9).
    up = kit_images.fitted_scale("cover", 1080, 1920, 807, 1435) * 1.12
    assert up <= 1.5
    assert results(make_kit(tmp_path, sizes=dict(SIZES, background=(807, 1435, 3))))["kit.images"].passed
    r = results(make_kit(tmp_path / "b", sizes=dict(SIZES, background=(806, 1433, 3))))["kit.images"]
    assert not r.passed and "background" in r.offenders[0] and r.metrics["upscale"]["background"] > 1.5


def test_messages_carry_no_machine_path(tmp_path):
    folder = make_kit(tmp_path)
    (folder / "hero.png").unlink()
    res = results(folder)
    assert all(str(tmp_path) not in json.dumps(r.to_dict()) for r in res.values())


def test_kit_structure_errors_are_tool_errors(tmp_path):
    with pytest.raises(ConventionError, match="match the folder name"):
        make_kit(tmp_path, with_change(lambda d: d["game"].__setitem__("id", "other")))
        km.load(str(tmp_path / "test_game"))
    folder = make_kit(tmp_path / "x", with_change(lambda d: d["strings"].__setitem__("English", {})))
    with pytest.raises(ConventionError, match="a locale like"):
        km.load(str(folder))
