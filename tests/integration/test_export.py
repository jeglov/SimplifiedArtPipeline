"""FBX export (Phase 2 step 3, backlog #27): validated scene -> canonical FBX
-> the FBX validated again, linked to its source by sha256."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from conftest import sha256

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from broken_meshes import EXPECT, FIX_EXPECT  # noqa: E402

# Every fixture that passes as-is or after --fix: all of them must export.
EXPORTABLE = sorted([n for n, (_, failing) in EXPECT.items() if not failing]
                    + [n for n, still in FIX_EXPECT.items() if not still])


@pytest.fixture(scope="session")
def export(maya_session, conventions):
    """export(path, asset_class, out_dir, fix_dir=None) -> (validated report, FBX report)."""
    from validate.maya.cli import export_file, validate_file, validated_file

    def run(path, asset_class, out_dir, fix_dir=None):
        cls = conventions.asset_class(asset_class)
        validated = validate_file(Path(path), conventions, cls, max_offenders=0, fix_dir=fix_dir)
        source = validated_file(Path(path), validated, fix_dir)
        return validated, export_file(source, validated, conventions, cls, Path(out_dir), max_offenders=0)
    return run


def failed_ids(report):
    return sorted(r.id for r in report.results if not r.passed)


@pytest.mark.parametrize("name", EXPORTABLE)
def test_every_passing_fixture_exports_to_a_passing_deterministic_fbx(name, scenes, export, tmp_path):
    asset_class = EXPECT[name][0]
    validated, fbx_report = export(scenes[name], asset_class, tmp_path / "a", fix_dir=tmp_path / "fixed_a")
    assert validated.exit_code == 0
    assert fbx_report.tool_error is None, fbx_report.tool_error
    assert failed_ids(fbx_report) == []

    fbx = tmp_path / "a" / (name + ".fbx")
    # The link the Unity gate relies on: report hash == file hash, and
    # exported_from == the validated scene's own report identity.
    assert fbx_report.source_sha256 == sha256(fbx)
    assert fbx_report.exported_from == {"path": validated.source_path, "sha256": validated.source_sha256}

    # Same scene, second export to another folder: same FBX bytes.
    _, again = export(scenes[name], asset_class, tmp_path / "b", fix_dir=tmp_path / "fixed_b")
    assert again.source_sha256 == fbx_report.source_sha256


def test_export_section_records_settings_and_file_facts(scenes, export, conventions, tmp_path):
    _, r = export(scenes["SM_Clean"], "prop_small", tmp_path)
    assert r.export == {"format": "fbx-ascii", "settings_version": conventions.fbx_export.settings_version,
                        "file_version": "FBX202000", "up_axis": "y", "unit_scale_cm": 1.0}


def test_fbx_has_no_cameras_lights_animation_or_paths(scenes, export, tmp_path):
    export(scenes["SM_Messy"], "prop_small", tmp_path / "out", fix_dir=tmp_path / "fixed")
    text = (tmp_path / "out" / "SM_Messy.fbx").read_text(encoding="utf-8")
    objects = set(re.findall(r'^\t(\w+): \d+, "', text, re.M))
    # Maya scenes always contain persp/top/front/side cameras: none may leak.
    assert objects <= {"Document", "Geometry", "Model", "Material"}, objects
    assert not re.search(r'"[A-Za-z]:\\', text)
    assert "\r" not in text


def test_fbx_is_triangulated(scenes, export, tmp_path):
    # SM_Clean is all quads in Maya.
    export(scenes["SM_Clean"], "prop_small", tmp_path)
    text = (tmp_path / "SM_Clean.fbx").read_text(encoding="utf-8")
    indices = [int(v) for v in re.search(r"PolygonVertexIndex: \*\d+ \{\s*a: ([^\n]+)", text).group(1).split(",")]
    ends = [i for i, v in enumerate(indices) if v < 0]  # FBX marks a polygon's last index as -(i+1)
    sizes = [b - a for a, b in zip([-1] + ends, ends)]
    assert sizes and set(sizes) == {3}


def test_skinned_fbx_keeps_its_skin(scenes, export, tmp_path):
    # Bind pose and deformers reference IDs outside plain declarations: the
    # renumbering must keep them connected, and the re-validation must pass.
    _, r = export(scenes["SK_Skinned"], "character", tmp_path)
    assert r.tool_error is None and failed_ids(r) == []
    text = (tmp_path / "SK_Skinned.fbx").read_text(encoding="utf-8")
    assert '"Deformer::' in text and '"BindPose"' in text


def test_failed_asset_is_never_exported(scenes, export, tmp_path):
    validated, r = export(scenes["SM_Ngon"], "prop_small", tmp_path)
    assert validated.exit_code == 1
    assert "refusing to export" in r.tool_error
    assert not any(tmp_path.iterdir())


def test_fbx_input_fixes_to_ma_then_exports(scenes, export, tmp_path):
    # Backlog #27: an FBX coming IN (e.g. from an AI generator) can be fixed
    # and goes OUT as a canonical FBX. The fixed working file is .ma.
    from maya import cmds
    from validate.maya import session

    session.open_scene(scenes["SM_Messy"])
    cmds.loadPlugin("fbxmaya", quiet=True)
    raw_fbx = tmp_path / "in" / "SM_Messy.fbx"
    raw_fbx.parent.mkdir()
    cmds.file(raw_fbx.as_posix(), force=True, exportAll=True, type="FBX export")

    validated, r = export(raw_fbx, "prop_small", tmp_path / "out", fix_dir=tmp_path / "fixed")
    assert validated.fixed_from is not None and validated.source_path.endswith("SM_Messy.ma")
    assert validated.exit_code == 0 and r.tool_error is None and failed_ids(r) == []


def test_export_refuses_to_overwrite_its_source(scenes, export, tmp_path):
    from validate.maya.export import export_fbx  # noqa: F401  (module must import under Maya)
    _, first = export(scenes["SM_Clean"], "prop_small", tmp_path)
    fbx = tmp_path / "SM_Clean.fbx"
    before = fbx.read_bytes()
    _, again = export(fbx, "prop_small", tmp_path)
    assert "refusing to overwrite" in again.tool_error
    assert fbx.read_bytes() == before


def test_unmapped_flag_fails_the_export(maya_session, conventions):
    # A new flag in fbx_export.yaml with no exporter command must stop the
    # export, never be silently skipped.
    from dataclasses import replace
    from validate.maya.export import ExportError, apply_settings
    rules = replace(conventions.fbx_export, flags=conventions.fbx_export.flags + (("bogus", True),))
    with pytest.raises(ExportError, match="bogus"):
        apply_settings(rules)


def test_ignored_setting_fails_the_export(maya_session, conventions, monkeypatch):
    # Simulate a plugin that accepts "FBXExportCameras -v false" but keeps
    # cameras on (renamed command, older plugin): the read-back must catch it.
    import maya.mel as mel
    from validate.maya.export import ExportError, apply_settings
    real = mel.eval

    def lying_eval(cmd):
        return 1 if cmd.strip() == "FBXExportCameras -q" else real(cmd)

    monkeypatch.setattr(mel, "eval", lying_eval)
    with pytest.raises(ExportError, match="cameras=True"):
        apply_settings(conventions.fbx_export)


def test_unit_mismatch_in_the_written_file_fails(scenes, conventions, tmp_path):
    # Conventions say metres, the scene (and so the file) is in cm: the check
    # reads UnitScaleFactor from the FILE and must refuse.
    from dataclasses import replace
    from validate.maya import session
    from validate.maya.export import ExportError, export_fbx
    session.open_scene(scenes["SM_Clean"])
    with pytest.raises(ExportError, match="UnitScaleFactor"):
        export_fbx(tmp_path / "SM_Clean.fbx", replace(conventions, linear_unit="m"))
    assert not (tmp_path / "SM_Clean.fbx").exists()  # nothing half-written left behind


def test_fixed_multi_mesh_scene_is_byte_stable(scenes, validate, tmp_path):
    # Maya saves "-na" set-member connections in varying order for multi-mesh
    # scenes (probed); without validate.core.ma_canonical the fixed file and
    # its report got a new sha256 on every run.
    a = validate(scenes["SM_GroupNotFrozen"], "prop_small", fix_dir=tmp_path / "a")
    b = validate(scenes["SM_GroupNotFrozen"], "prop_small", fix_dir=tmp_path / "b")
    assert a.fixed_from is not None
    assert a.source_sha256 == b.source_sha256
