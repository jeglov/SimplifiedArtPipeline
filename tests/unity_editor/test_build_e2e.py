"""Step 8: `artpipe build`, from a Maya scene to a Unity prefab, end to end.

Real processes: a mayapy for the Maya stage, a batchmode Unity for the
import, exactly as an agent calls them. Slow (marker "unity").
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline import build, unity as u
from unity_helpers import png
from validate.core import conventions as conv

jsonschema = pytest.importorskip("jsonschema", reason="pip install -r requirements-maya.txt")
SCHEMA = jsonschema.Draft202012Validator(json.loads(
    (conv.DEFAULT_DIR / "build_report.schema.json").read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def scenes(tmp_path_factory):
    """The Phase 1 fixture scenes, built in their own mayapy process."""
    out = tmp_path_factory.mktemp("scenes")
    subprocess.run([sys.executable, str(u.ROOT / "tests" / "fixtures" / "broken_meshes.py"), str(out)],
                   cwd=str(u.ROOT), check=True, stdout=subprocess.DEVNULL)
    return out


def run_build(args, out: Path):
    code = build.main(args + ["--out", str(out)])
    stem = Path(args[0]).stem
    raw = (out / stem / "build.report.json").read_bytes()
    data = json.loads(raw)
    problems = sorted("%s: %s" % ("/".join(map(str, e.path)), e.message) for e in SCHEMA.iter_errors(data))
    assert not problems, problems
    return code, data, raw


@pytest.fixture(scope="module")
def messy(scenes, project_root_cleanup, tmp_path_factory):
    texture = png(tmp_path_factory.mktemp("tex") / "T_Messy_D.png", 128, alpha=True)
    out = tmp_path_factory.mktemp("build")
    args = [str(scenes / "SM_Messy.ma"), "--class", "prop_small", "--texture", str(texture)]
    return args, out, run_build(args, out)


def test_messy_scene_comes_out_as_a_clean_prefab(messy):
    _, _, (code, data, _) = messy
    assert code == 0 and data["status"] == "pass" and data["stopped_at"] is None
    maya, unity_stage = data["stages"]["maya"], data["stages"]["unity"]
    assert maya["fixes_applied"] == 6  # default names, UV set, history, transform, pivot, units
    assert unity_stage["prefab"] == "Assets/Art/prop_small/SM_Messy.prefab"
    assert (u.PROJECT / unity_stage["prefab"]).is_file()
    assert unity_stage["textures"] == ["Assets/Art/prop_small/T_Messy_D.png"]


def test_stage_reports_are_linked_by_hash(messy):
    _, _, (_, data, _) = messy
    maya = data["stages"]["maya"]
    fbx_report = json.loads((u.ROOT / maya["fbx_report"]).read_text(encoding="utf-8"))
    assert fbx_report["source"]["sha256"] == maya["fbx_sha256"]
    unity_report = json.loads((u.ROOT / data["stages"]["unity"]["report"]).read_text(encoding="utf-8"))
    model = next(a for a in unity_report["assets"] if a["kind"] == "model")
    assert model["source"]["sha256"] == maya["fbx_sha256"]
    assert next(r for r in model["results"] if r["id"] == "unity.prefab")["passed"]


def test_rebuild_is_byte_identical(messy):
    args, out, (_, _, raw) = messy
    code, _, again = run_build(args, out)
    assert code == 0 and again == raw


def test_unfixable_scene_stops_in_maya_and_never_reaches_unity(scenes, project_root_cleanup, tmp_path):
    # An n-gon is never auto-fixed: a human decides how to re-topologize.
    code, data, _ = run_build([str(scenes / "SM_Ngon.ma"), "--class", "prop_small"], tmp_path)
    assert code == 1 and data["status"] == "fail" and data["stopped_at"] == "maya"
    assert data["stages"]["unity"] is None and data["stages"]["maya"]["fbx"] is None
    assert not (u.PROJECT / "Assets/Art/prop_small/SM_Ngon.fbx").exists()


def test_missing_input_is_a_tool_error(tmp_path):
    assert build.main([str(tmp_path / "nope.ma"), "--class", "prop_small", "--out", str(tmp_path)]) == 2


def test_recreated_prefab_is_byte_identical(messy):
    # A rebuild keeps an existing prefab (someone may have edited it), so
    # determinism of a NEW prefab needs a delete first. Probed: Unity gives a
    # new variant a random PrefabInstance file ID; the CLI pins it.
    args, out, (_, data, _) = messy
    prefab = u.PROJECT / data["stages"]["unity"]["prefab"]
    meta = prefab.with_name(prefab.name + ".meta")
    before = (prefab.read_bytes(), meta.read_bytes())
    prefab.unlink()
    meta.unlink()
    code, _, _ = run_build(args, out)
    assert code == 0
    assert (prefab.read_bytes(), meta.read_bytes()) == before
