"""conventions/report.schema.json (backlog #15): the report contract, enforced.

Pure Python. Every real report the Maya suite produces is also validated, in
tests/integration/test_report_contract.py.
"""
from __future__ import annotations

import copy
import json

import pytest

from validate.core import conventions as conv
from validate.core.result import SCHEMA_VERSION, CheckResult, FixResult, Report, Severity

jsonschema = pytest.importorskip("jsonschema", reason="pip install -r requirements-maya.txt")

SCHEMA = json.loads((conv.DEFAULT_DIR / "report.schema.json").read_text(encoding="utf-8"))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
SHA = "ab" * 32


def errors(doc):
    return sorted(e.message for e in VALIDATOR.iter_errors(doc))


def sample(**kw):
    ok = CheckResult("topology.ngons", True, Severity.ERROR, "no ngons", False)
    bad = CheckResult("budget.tris", False, Severity.ERROR, "600 > 500", False,
                      metrics={"tris": 600, "ratio": 1.2}, offenders=["|SM_Crate"])
    return Report("out/SM_Crate.ma", SHA, "prop_small", results=[ok, bad], **kw).to_dict()


def test_schema_itself_is_valid_draft_2020_12():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)


def test_schema_version_matches_the_code():
    assert SCHEMA["properties"]["schema_version"]["const"] == SCHEMA_VERSION


@pytest.mark.parametrize("kw", [
    {},
    {"tool_error": "could not open file"},
    {"fixes": [FixResult("scene.history", True, "baked", changed=["|SM_Crate"]),
               FixResult("transform.frozen", False, "negative scale")],
     "fixed_from": {"path": "in/SM_Crate.ma", "sha256": SHA}},
    {"exported_from": {"path": "out/fixed/SM_Crate.ma", "sha256": SHA},
     "export": {"format": "fbx-ascii", "settings_version": 1, "file_version": "FBX202000",
                "up_axis": "y", "unit_scale_cm": 1.0}},
], ids=["plain", "tool_error", "fix_mode", "export_mode"])
def test_every_report_kind_validates(kw):
    assert errors(sample(**kw)) == []


def test_unreadable_source_has_empty_hash():
    # Boundary: "" is allowed (file not found), any other non-hex is not.
    assert errors(Report("missing.ma", "", "prop_small", tool_error="file not found").to_dict()) == []


@pytest.mark.parametrize("mutate, fragment", [
    (lambda d: d.__setitem__("extra", 1), "Additional properties"),
    (lambda d: d.pop("exported_from"), "'exported_from' is a required property"),
    (lambda d: d.__setitem__("schema_version", "1.1"), "'1.2' was expected"),
    (lambda d: d.__setitem__("status", "ok"), "is not one of"),
    (lambda d: d["source"].__setitem__("sha256", "XYZ"), "does not match"),
    (lambda d: d["results"][0].__setitem__("severity", "fatal"), "is not one of"),
    (lambda d: d["results"][0].__setitem__("id", "ngons"), "does not match"),
    (lambda d: d["summary"].__setitem__("errors", -1), "less than the minimum"),
    (lambda d: d.__setitem__("export", {"format": "fbx-binary"}), "is not valid under any"),
])
def test_contract_violations_are_caught(mutate, fragment):
    doc = copy.deepcopy(sample())
    mutate(doc)
    assert any(fragment in e for e in errors(doc)), errors(doc)
