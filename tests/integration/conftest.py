"""Shared setup for the Maya integration tests.

One Maya boot for the whole session (~20 s), one build of every fixture
scene into a temp folder, conventions loaded once. Under plain Python (no
mayapy) every test in this folder is skipped, so the pure-core tests still
run anywhere, e.g. on a CI machine without a Maya licence.

The folder is deliberately NOT called "maya": pytest puts test folders on
sys.path, and a "maya" folder could shadow Maya's own package.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

pytest.importorskip("maya.standalone", reason="needs mayapy (run with: artpipe test)")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

import broken_meshes  # noqa: E402  (needs the sys.path entry above)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pytest_collection_modifyitems(items):
    here = Path(__file__).parent
    for item in items:
        if here in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.maya)


@pytest.fixture(scope="session")
def maya_session():
    import maya.standalone
    maya.standalone.initialize(name="python")
    yield
    maya.standalone.uninitialize()


@pytest.fixture(scope="session")
def conventions():
    from validate.core import conventions as conv
    return conv.load()


@pytest.fixture(scope="session")
def scenes(maya_session, tmp_path_factory):
    """{fixture name: path to its .ma}, built once per session.

    Also records each file's sha256, so tests can prove sources are never modified.
    """
    out = tmp_path_factory.mktemp("fixtures")
    paths = {name: Path(p) for name, p in broken_meshes.build_all(str(out)).items()}
    return paths


@pytest.fixture(scope="session")
def source_hashes(scenes):
    return {name: sha256(path) for name, path in scenes.items()}


@pytest.fixture(scope="session")
def validate(maya_session, conventions):
    """validate(path, asset_class, fix_dir=None) -> Report, in this Maya session."""
    from validate.maya.cli import validate_file

    def run(path, asset_class, fix_dir=None, max_offenders=0):
        return validate_file(Path(path), conventions, conventions.asset_class(asset_class),
                             max_offenders=max_offenders, fix_dir=fix_dir)
    return run
