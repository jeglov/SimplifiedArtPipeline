"""Step 7: `artpipe unity import|verify` end to end, through the same entry
point an agent uses. Boots Unity ~5 times (slow, marker "unity").

Imports into the real project, so a module fixture removes everything it
added under the managed root afterwards.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from unity_helpers import png
from pipeline import unity as u
from validate.core import conventions as conv

GOLDENS = u.PROJECT / "Packages" / "com.artpipe.importer" / "Tests" / "Editor" / "Fixtures~"
ROOT = u.PROJECT / conv.load().unity_import.root  # unity/Assets/Art

jsonschema = pytest.importorskip("jsonschema", reason="pip install -r requirements-maya.txt")
SCHEMA = jsonschema.Draft202012Validator(json.loads(
    (conv.DEFAULT_DIR / "unity_report.schema.json").read_text(encoding="utf-8")))


def run(args, report: Path):
    code = u.main(args + ["--report", str(report)])
    data = json.loads(report.read_text(encoding="utf-8"))
    problems = sorted("%s: %s" % ("/".join(map(str, e.path)), e.message) for e in SCHEMA.iter_errors(data))
    assert not problems, problems
    return code, data


def asset(data, name):
    return next(a for a in data["assets"] if a["path"].endswith("/" + name))


def failed(entry):
    return sorted(r["id"] for r in entry["results"] if not r["passed"] and r["severity"] == "error")


@pytest.fixture(scope="module")
def inbox(tmp_path_factory, project_root_cleanup):
    d = tmp_path_factory.mktemp("inbox")
    for name in ("SM_Clean", "SM_Messy", "SM_LodsGood"):
        for suffix in (".fbx", ".fbx.report.json"):
            shutil.copy(str(GOLDENS / (name + suffix)), str(d / (name + suffix)))
    shutil.copy(str(GOLDENS / "SM_Clean.fbx"), str(d / "SM_Unchecked.fbx"))  # no sidecar
    png(d / "T_Crate_D.png", 128, alpha=True)
    return d


@pytest.fixture(scope="module")
def first_import(inbox, tmp_path_factory):
    report = tmp_path_factory.mktemp("r") / "import.json"
    files = [str(inbox / f) for f in ("SM_Clean.fbx", "SM_Messy.fbx", "SM_Unchecked.fbx", "T_Crate_D.png")]
    return run(["import", "--class", "prop_small"] + files, report)


def test_import_accepts_gated_assets_and_refuses_the_rest(first_import):
    code, data = first_import
    assert code == u.EXIT_FAIL  # SM_Unchecked was refused
    assert data["mode"] == "import" and data["status"] == "fail"
    for name in ("SM_Clean.fbx", "SM_Messy.fbx", "T_Crate_D.png"):
        entry = asset(data, name)
        assert entry["status"] == "pass" and entry["imported"], (name, failed(entry))
        assert (u.PROJECT / entry["path"]).is_file()
    refused = asset(data, "SM_Unchecked.fbx")
    assert refused["imported"] is False and failed(refused) == ["gate.report_present"]
    # The inbox gate: a refused file never enters the project.
    assert not (u.PROJECT / refused["path"]).exists()


def test_report_traces_each_asset_to_its_source(first_import, inbox):
    _, data = first_import
    entry = asset(data, "SM_Messy.fbx")
    assert entry["source"]["sha256"] == hashlib.sha256((inbox / "SM_Messy.fbx").read_bytes()).hexdigest()
    assert entry["asset_class"] == "prop_small"
    ids = [r["id"] for r in entry["results"]]
    assert ids[:6] == ["gate.report_present", "gate.report_readable", "gate.report_passed",
                       "gate.hash_match", "gate.export_settings", "gate.asset_class"]
    assert "unity.vertex_budget" in ids


@pytest.mark.parametrize("name", ["SM_Clean.fbx", "SM_Clean.fbx.report.json", "T_Crate_D.png"])
def test_new_assets_get_deterministic_guids_and_full_metas(first_import, name):
    _, data = first_import
    path = asset(data, "SM_Clean.fbx")["path"].rsplit("/", 1)[0] + "/" + name
    meta = (u.PROJECT / (path + ".meta")).read_text(encoding="utf-8")
    expected = hashlib.md5(("artpipe:" + path).encode("utf-8")).hexdigest()
    assert "guid: %s" % expected in meta
    # Probed: a GUID-only .meta makes Unity skip writing importer settings
    # (a PNG then imported as a Cubemap). The meta must be Unity's full one.
    assert len(meta.splitlines()) > 5, meta


def test_texture_gets_real_astc(first_import):
    _, data = first_import
    fmt = next(r for r in asset(data, "T_Crate_D.png")["results"] if r["id"] == "texture.platform_format")
    assert fmt["passed"] and fmt["metrics"]["verified"] is True


def test_reimport_is_idempotent_and_deterministic(first_import, inbox, tmp_path):
    files = [str(inbox / f) for f in ("SM_Clean.fbx", "SM_Messy.fbx", "SM_Unchecked.fbx", "T_Crate_D.png")]
    metas = {p: p.read_bytes() for p in ROOT.rglob("*.meta")}
    code, _ = run(["import", "--class", "prop_small"] + files, tmp_path / "again.json")
    assert code == first_import[0]
    first = json.dumps(first_import[1], sort_keys=True)
    assert json.dumps(json.loads((tmp_path / "again.json").read_text(encoding="utf-8")), sort_keys=True) == first
    assert {p: p.read_bytes() for p in ROOT.rglob("*.meta")} == metas, ".meta files changed on reimport"


def test_faceted_lod_fails_after_import(inbox, first_import, tmp_path):
    code, data = run(["import", "--class", "prop_medium", str(inbox / "SM_LodsGood.fbx")], tmp_path / "lods.json")
    assert code == u.EXIT_FAIL
    assert failed(asset(data, "SM_LodsGood.fbx")) == ["unity.lod_vertex_ratio"]


def test_verify_rechecks_everything_in_the_project(test_faceted_lod_dependency, tmp_path):
    code, data = run(["verify"], tmp_path / "verify.json")
    assert data["mode"] == "verify"
    paths = sorted(a["path"].rsplit("/", 1)[1] for a in data["assets"])
    assert paths == ["SM_Clean.fbx", "SM_LodsGood.fbx", "SM_Messy.fbx", "T_Crate_D.png"]
    assert code == u.EXIT_FAIL and data["summary"]["failed"] == 1


@pytest.fixture
def test_faceted_lod_dependency(inbox, first_import, tmp_path):
    # verify must see the LOD asset too: make sure it was imported.
    run(["import", "--class", "prop_medium", str(inbox / "SM_LodsGood.fbx")], tmp_path / "lods.json")


def test_unknown_class_is_a_tool_error(inbox, project_root_cleanup, tmp_path):
    code, data = run(["import", "--class", "prop_tiny", str(inbox / "SM_Clean.fbx")], tmp_path / "bad.json")
    assert code == u.EXIT_TOOL_ERROR
    assert data["status"] == "error" and "prop_tiny" in data["tool_error"]
