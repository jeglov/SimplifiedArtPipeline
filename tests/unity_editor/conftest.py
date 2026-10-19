"""Shared setup for the tests that boot the real Unity Editor (batchmode).

Slow (one Editor boot is ~15-20 s warm, minutes cold). Skipped when the Unity
version pinned by unity/ProjectSettings/ProjectVersion.txt isn't installed,
so the rest of the suite runs on any machine.
Fast run without them: artpipe test -m "not unity"

The folder is deliberately NOT called "unity": pytest puts test folders on
sys.path, and a "unity" folder could shadow the unity/ project path logic.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import unity as u

try:
    u.find_unity()
except u.ToolError as exc:
    pytest.skip("needs Unity: %s" % exc, allow_module_level=True)


def pytest_collection_modifyitems(items):
    here = Path(__file__).parent
    for item in items:
        if here in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.unity)
