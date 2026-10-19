"""Every report the real Maya validator writes matches conventions/report.schema.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from validate.core import conventions as conv

jsonschema = pytest.importorskip("jsonschema", reason="pip install -r requirements-maya.txt")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from broken_meshes import EXPECT  # noqa: E402

VALIDATOR = jsonschema.Draft202012Validator(
    json.loads((conv.DEFAULT_DIR / "report.schema.json").read_text(encoding="utf-8")))


def assert_valid(report):
    # Through JSON text, exactly as a consumer reads it.
    doc = json.loads(report.to_json())
    problems = sorted("%s: %s" % ("/".join(map(str, e.path)), e.message) for e in VALIDATOR.iter_errors(doc))
    assert not problems, problems


@pytest.mark.parametrize("name", sorted(EXPECT))
def test_validate_and_fix_reports_match_the_schema(name, scenes, validate, tmp_path):
    asset_class = EXPECT[name][0]
    assert_valid(validate(scenes[name], asset_class))
    assert_valid(validate(scenes[name], asset_class, fix_dir=tmp_path))


def test_export_and_refusal_reports_match_the_schema(scenes, conventions, maya_session, tmp_path):
    from validate.maya.cli import export_file, validate_file
    cls = conventions.asset_class("prop_small")
    ok = validate_file(scenes["SM_Clean"], conventions, cls)
    assert_valid(export_file(scenes["SM_Clean"], ok, conventions, cls, tmp_path))
    bad = validate_file(scenes["SM_Ngon"], conventions, cls)
    assert_valid(export_file(scenes["SM_Ngon"], bad, conventions, cls, tmp_path))
