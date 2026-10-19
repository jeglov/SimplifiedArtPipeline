"""Tests for validate.core.paths - no Maya required."""
import os
from pathlib import Path

from validate.core.paths import REPO_ROOT, report_path


def test_inside_repo_is_relative_posix():
    assert report_path(REPO_ROOT / "out" / "fixtures" / "SM_Crate.ma") == "out/fixtures/SM_Crate.ma"


def test_same_file_same_path_from_any_cwd(tmp_path, monkeypatch):
    target = REPO_ROOT / "conventions" / "mesh_budgets.yaml"
    from_root = report_path(target)
    monkeypatch.chdir(tmp_path)
    relative_from_elsewhere = os.path.relpath(target, tmp_path)
    assert report_path(relative_from_elsewhere) == from_root == "conventions/mesh_budgets.yaml"


def test_outside_repo_is_absolute_posix(tmp_path):
    out = report_path(tmp_path / "x.fbx")
    assert out == (tmp_path / "x.fbx").resolve().as_posix()
    assert "\\" not in out
    assert Path(out).is_absolute()
