"""Maya integration tests: real scenes in, real reports out.

Proves, for every fixture in tests/fixtures/broken_meshes.py:
  * it fails EXACTLY the checks in EXPECT (each check catches its case, and
    no other check fires: no false positives)
  * --fix ends at FIX_EXPECT, never touches the source, and is idempotent
And for the pipeline as a whole: every check and fix is exercised,
reports are byte-identical across runs, checks don't modify the scene,
broken inputs give a tool_error, and FBX input works.

Run: artpipe test (or artpipe test tests/integration -v)
"""
from __future__ import annotations

import pytest

from broken_meshes import EXPECT, FIX_EXPECT
from conftest import sha256

NAMES = sorted(EXPECT)
FIXED = sorted(FIX_EXPECT)


def failed_ids(report):
    return sorted(r.id for r in report.results if not r.passed)


# --- every check catches its case -----------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_fixture_fails_exactly_expected_checks(name, scenes, validate):
    asset_class, expected = EXPECT[name]
    report = validate(scenes[name], asset_class)
    assert report.tool_error is None
    assert failed_ids(report) == sorted(expected)


def test_every_check_is_proven_by_a_fixture(maya_session):
    from validate.maya import registry
    caught = {check for _, checks in EXPECT.values() for check in checks}
    untested = sorted(c.ID for c in registry.CHECKS if c.ID not in caught)
    assert not untested, "no fixture fails these checks: %s" % untested


def test_every_fix_is_proven_by_a_fixture(maya_session):
    from validate.maya import registry
    fixed = {c for name in FIX_EXPECT for c in set(EXPECT[name][1]) - set(FIX_EXPECT[name])}
    untested = sorted(f.ID for f in registry.FIXES if f.ID not in fixed)
    assert not untested, "no fixture is repaired by these fixes: %s" % untested


# --- auto-fix --------------------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_fix_mode(name, scenes, source_hashes, validate, tmp_path):
    asset_class, before = EXPECT[name]
    report = validate(scenes[name], asset_class, fix_dir=tmp_path)
    assert report.tool_error is None
    assert failed_ids(report) == sorted(FIX_EXPECT.get(name, before))
    if name in FIX_EXPECT:
        # A fixed copy was saved and re-validated; the report points back.
        assert report.fixed_from["sha256"] == source_hashes[name]
        assert report.source_path.endswith("/%s.ma" % name)
        assert (tmp_path / ("%s.ma" % name)).is_file()
    else:
        assert report.fixed_from is None and not any(tmp_path.iterdir())
    assert sha256(scenes[name]) == source_hashes[name], "the source file was modified"


@pytest.mark.parametrize("name", FIXED)
def test_fix_is_idempotent(name, scenes, validate, tmp_path):
    asset_class = EXPECT[name][0]
    validate(scenes[name], asset_class, fix_dir=tmp_path / "first")
    again = validate(tmp_path / "first" / ("%s.ma" % name), asset_class, fix_dir=tmp_path / "second")
    assert [f.id for f in again.fixes if f.applied] == []
    assert again.fixed_from is None and not (tmp_path / "second").exists()


def test_blocked_fixes_explain_why(scenes, validate, tmp_path):
    reasons = {
        "SM_NegativeScale": ("transform.frozen", "negative scale"),
        "SK_SkinnedLamina": ("topology.lamina_faces", "skinned"),
        "SM_FileMismatch": ("naming.convention", "unclear which one is right"),
        "SM_NoUVs": ("uv.present", "needs an unwrap"),
    }
    for name, (fix_id, words) in reasons.items():
        report = validate(scenes[name], EXPECT[name][0], fix_dir=tmp_path)
        skipped = {f.id: f for f in report.fixes if not f.applied}
        assert fix_id in skipped, name
        assert words in skipped[fix_id].message, (name, skipped[fix_id].message)
        failing = {r.id: r for r in validate(scenes[name], EXPECT[name][0]).results if not r.passed}
        assert failing[fix_id].fixable is False, "%s: blocked fix must report fixable=false" % name


# --- contracts -------------------------------------------------------------------

@pytest.mark.parametrize("name", ["SM_Clean", "SM_Messy", "SK_SkinnedLamina", "SM_UVSetOrder"])
def test_reports_are_byte_identical(name, scenes, validate):
    asset_class = EXPECT[name][0]
    assert validate(scenes[name], asset_class).to_json() == validate(scenes[name], asset_class).to_json()


def test_fixed_files_are_byte_identical(scenes, validate, tmp_path):
    for run in ("a", "b"):
        validate(scenes["SM_Messy"], "prop_small", fix_dir=tmp_path / run)
    assert (tmp_path / "a" / "SM_Messy.ma").read_bytes() == (tmp_path / "b" / "SM_Messy.ma").read_bytes()


@pytest.mark.parametrize("name", ["SM_UVSetOrder", "SM_Messy", "SK_Skinned", "SM_Lamina"])
def test_checks_do_not_modify_the_scene(name, scenes, conventions):
    from maya import cmds
    from validate.maya import context, registry, session

    session.open_scene(scenes[name])
    asset_class = conventions.asset_class(EXPECT[name][0])

    def state():
        meshes = cmds.ls(type="mesh", long=True) or []
        return (sorted(cmds.ls()), cmds.ls(selection=True),
                {m: cmds.polyUVSet(m, query=True, currentUVSet=True) for m in meshes},
                {t: cmds.xform(t, query=True, matrix=True, worldSpace=True)
                 for t in cmds.ls(type="transform", long=True)})

    before = state()
    registry.run_all(context.build(str(scenes[name]), conventions, asset_class))
    assert state() == before


def test_missing_file_is_a_tool_error(maya_session, validate, tmp_path):
    report = validate(tmp_path / "SM_Nope.ma", "prop_small")
    assert report.status.value == "error" and "file not found" in report.tool_error
    assert report.exit_code == 2


def test_unsupported_file_type_is_a_tool_error(maya_session, validate, tmp_path):
    bad = tmp_path / "SM_Crate.obj"
    bad.write_text("v 0 0 0\n")
    report = validate(bad, "prop_small")
    assert report.status.value == "error" and "unsupported file type" in report.tool_error


def test_fbx_input_round_trip(scenes, validate, tmp_path):
    from maya import cmds
    from validate.maya import session

    session.open_scene(scenes["SM_Clean"])
    cmds.loadPlugin("fbxmaya", quiet=True)
    fbx = tmp_path / "SM_Clean.fbx"
    cmds.file(fbx.as_posix(), force=True, exportAll=True, type="FBX export")
    report = validate(fbx, "prop_small")
    assert report.tool_error is None and failed_ids(report) == []
    units = next(r for r in report.results if r.id == "scene.units")
    assert units.metrics["verified"] is False  # FBX units can't be verified yet (backlog #4)
