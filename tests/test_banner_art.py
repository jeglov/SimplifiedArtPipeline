"""banners.art: kit images with provenance sidecars. Hero on a synthetic concept run (pure);
logo on the real pinned Chromium (marker render)."""
from __future__ import annotations

import json
import shutil

import jsonschema
import pytest

from banners import art, tools
from banners.core import ToolError
from banners.core import kit as km
from banners.core import rules as br
from validate.core.conventions import DEFAULT_DIR
from validate.core.image import decode_png, encode_png

from test_banner_cutout import scene

SCHEMA = json.loads((DEFAULT_DIR / "banner_provenance.schema.json").read_text(encoding="utf-8"))


def concept_run(tmp_path, status="pass"):
    run = tmp_path / "concept_zimage-abc"
    run.mkdir()
    img, mask = scene()
    (run / "image.png").write_bytes(encode_png(img.width, img.height, 3, img.data))
    (run / "mask.png").write_bytes(encode_png(mask.width, mask.height, 1, mask.data))
    (run / "concept.report.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    (run / "manifest.json").write_text(json.dumps({
        "status": "ok", "run_id": "concept_zimage-abc", "params": {"prompt": "a red box", "seed": 3},
        "models": [{"id": "z_image_turbo_int8", "sha256": "a" * 64, "licence": "Apache-2.0", "file": "x"}]}),
        encoding="utf-8")
    return run


def test_hero_writes_image_and_a_valid_sidecar(tmp_path):
    out = art.hero(tmp_path, concept_run(tmp_path))
    rec = json.loads(km.provenance_path(out).read_text(encoding="utf-8"))
    jsonschema.validate(rec, SCHEMA)
    assert rec["output_sha256"] == tools.sha256_file(out) and rec["kind"] == "cutout"
    assert rec["from"]["seed"] == 3 and rec["models"] == [{"id": "z_image_turbo_int8", "sha256": "a" * 64,
                                                           "licence": "Apache-2.0"}]
    hero = decode_png(out)
    assert hero.channels == 4 and (hero.width, hero.height) == (12, 12)


def test_hero_refuses_a_failed_concept(tmp_path):
    with pytest.raises(ToolError, match="failed its checks"):
        art.hero(tmp_path, concept_run(tmp_path, status="fail"))


def test_hero_refuses_a_folder_that_is_not_a_run(tmp_path):
    with pytest.raises(ToolError, match="manifest"):
        art.hero(tmp_path, tmp_path)


RULES = br.load()
try:
    tools.require(RULES)
    MISSING = None
except ToolError as exc:
    MISSING = str(exc)


@pytest.mark.render
@pytest.mark.skipif(MISSING is not None, reason=MISSING or "")
def test_logo_is_rendered_transparent_and_cropped(tmp_path):
    kit = tmp_path / "spotcap_grove"
    shutil.copytree(str(km.KIT_DIR / "spotcap_grove"), str(kit))
    out = art.logo(kit, RULES)
    logo = decode_png(out)
    assert logo.channels == 4 and logo.alpha(0, 0) == 0          # transparent corner
    assert logo.width < art.LOGO_SIZE[0] and logo.height < art.LOGO_SIZE[1]
    rec = json.loads(km.provenance_path(out).read_text(encoding="utf-8"))
    jsonschema.validate(rec, SCHEMA)
    assert rec["licence"] == "own-work" and rec["from"]["fonts"][0]["licence"] == "OFL-1.1"
    # Same source, same font, same browser: the committed logo is this file.
    assert out.read_bytes() == (km.KIT_DIR / "spotcap_grove" / "logo.png").read_bytes()
