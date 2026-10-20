"""Backlog #49: `artpipe unity import` creates the shared material of a model
from the textures imported with it. Real batchmode Unity (marker "unity").

M_<Name> in the FBX + T_<Name>_D (+ _N) in the same import ->
Assets/Art/Materials/M_<Name>.mat, URP Lit, base and normal map set, a
deterministic GUID that the FBX's remap points at. Never overwritten.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest

from pipeline import unity as u
from unity_helpers import png
from validate.core import conventions as conv

GOLDENS = u.PROJECT / "Packages" / "com.artpipe.importer" / "Tests" / "Editor" / "Fixtures~"
MATERIALS = conv.load().unity_import.materials  # Assets/Art/Materials
MAT = MATERIALS + "/M_PickupCentered.mat"

jsonschema = pytest.importorskip("jsonschema", reason="pip install -r requirements-maya.txt")
SCHEMA = jsonschema.Draft202012Validator(json.loads(
    (conv.DEFAULT_DIR / "unity_report.schema.json").read_text(encoding="utf-8")))


def run(args, report: Path):
    code = u.main(args + ["--report", str(report)])
    data = json.loads(report.read_text(encoding="utf-8"))
    problems = sorted("%s: %s" % ("/".join(map(str, e.path)), e.message) for e in SCHEMA.iter_errors(data))
    assert not problems, problems
    return code, data


def model(data):
    return next(a for a in data["assets"] if a["kind"] == "model")


def guid(asset_path: str) -> str:
    text = (u.PROJECT / (asset_path + ".meta")).read_text(encoding="utf-8")
    return re.search(r"^guid: ([0-9a-f]{32})$", text, re.M).group(1)


def deterministic(asset_path: str) -> str:
    return hashlib.md5(("artpipe:" + asset_path).encode("utf-8")).hexdigest()


@pytest.fixture(scope="module")
def imported(tmp_path_factory, project_root_cleanup):
    d = tmp_path_factory.mktemp("inbox")
    for suffix in (".fbx", ".fbx.report.json"):
        shutil.copy(str(GOLDENS / ("SM_PickupCentered" + suffix)), str(d / ("SM_PickupCentered" + suffix)))
    files = [d / "SM_PickupCentered.fbx", png(d / "T_PickupCentered_D.png", 128, alpha=False),
             png(d / "T_PickupCentered_N.png", 128, alpha=False)]
    report = tmp_path_factory.mktemp("r") / "import.json"
    return d, files, run(["import", "--class", "pickup"] + [str(f) for f in files], report)


def test_material_is_created_and_the_model_uses_it(imported):
    _, _, (code, data) = imported
    assert code == 0, [r for a in data["assets"] for r in a["results"] if not r["passed"]]
    m = model(data)
    assert m["materials"] == [MAT]
    assert next(r for r in m["results"] if r["id"] == "unity.materials")["passed"]
    assert all(a["materials"] == [] for a in data["assets"] if a["kind"] == "texture")


def test_material_is_urp_lit_with_base_and_normal_maps(imported):
    text = (u.PROJECT / MAT).read_text(encoding="utf-8")
    d_guid, n_guid = guid("Assets/Art/pickup/T_PickupCentered_D.png"), guid("Assets/Art/pickup/T_PickupCentered_N.png")
    base = re.search(r"- _BaseMap:\s*\n\s*m_Texture: \{fileID: \d+, guid: ([0-9a-f]{32})", text)
    bump = re.search(r"- _BumpMap:\s*\n\s*m_Texture: \{fileID: \d+, guid: ([0-9a-f]{32})", text)
    assert base and base.group(1) == d_guid
    assert bump and bump.group(1) == n_guid
    assert "_NORMALMAP" in text


def test_material_guid_is_deterministic_and_the_fbx_remap_points_at_it(imported):
    assert guid(MAT) == deterministic(MAT)
    fbx_meta = (u.PROJECT / "Assets/Art/pickup/SM_PickupCentered.fbx.meta").read_text(encoding="utf-8")
    assert "guid: " + deterministic(MAT) in fbx_meta.split("externalObjects:")[1].split("materials:")[0]


def test_reimport_never_rewrites_the_material(imported, tmp_path):
    _, files, _ = imported
    before = (u.PROJECT / MAT).read_bytes(), (u.PROJECT / (MAT + ".meta")).read_bytes()
    code, data = run(["import", "--class", "pickup"] + [str(f) for f in files], tmp_path / "again.json")
    assert code == 0 and model(data)["materials"] == [MAT]
    assert ((u.PROJECT / MAT).read_bytes(), (u.PROJECT / (MAT + ".meta")).read_bytes()) == before


def test_textures_of_another_asset_make_no_material(imported, tmp_path):
    # Boundary: a base map named for another asset is not this model's material.
    d, _, _ = imported
    for suffix in (".fbx", ".fbx.report.json"):
        shutil.copy(str(GOLDENS / ("SM_Clean" + suffix)), str(d / ("SM_Clean" + suffix)))
    files = [d / "SM_Clean.fbx", png(d / "T_Crate_D.png", 128, alpha=False)]
    code, data = run(["import", "--class", "prop_small"] + [str(f) for f in files], tmp_path / "other.json")
    assert model(data)["materials"] == []
    assert not (u.PROJECT / MATERIALS / "M_Clean.mat").exists()
    warn = next(r for r in model(data)["results"] if r["id"] == "unity.materials")
    assert not warn["passed"] and warn["severity"] == "warning"
