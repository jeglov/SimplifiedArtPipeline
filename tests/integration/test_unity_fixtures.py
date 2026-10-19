"""The committed Unity golden fixtures (FBX + report) are what the pipeline
produces today. If this fails: artpipe unity-fixtures, then commit."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
import unity_fixtures  # noqa: E402


def without_paths(report_path: Path) -> dict:
    # Paths say where a file was written (repo folder vs this test's temp
    # folder); identity is the sha256, which must match exactly.
    doc = json.loads(report_path.read_text(encoding="utf-8"))
    doc["source"].pop("path")
    if doc["exported_from"]:
        doc["exported_from"].pop("path")
    return doc


@pytest.fixture(scope="module")
def regenerated(scenes, maya_session, tmp_path_factory):
    out = tmp_path_factory.mktemp("goldens")
    return unity_fixtures.generate({k: Path(v) for k, v in scenes.items()}, out / "fbx", out / "fixed")


@pytest.mark.parametrize("name", sorted(unity_fixtures.FIXTURES))
def test_committed_golden_is_fresh(name, regenerated):
    fbx, report = regenerated[name]
    committed = unity_fixtures.OUT / fbx.name
    assert committed.is_file(), "missing golden %s: run artpipe unity-fixtures" % committed.name
    assert committed.read_bytes() == fbx.read_bytes(), "stale %s: run artpipe unity-fixtures" % fbx.name
    assert without_paths(unity_fixtures.OUT / report.name) == without_paths(report)


def test_no_stray_files_in_the_golden_folder():
    expected = {n + ext for n in unity_fixtures.FIXTURES for ext in (".fbx", ".fbx.report.json")}
    assert {p.name for p in unity_fixtures.OUT.iterdir()} == expected
