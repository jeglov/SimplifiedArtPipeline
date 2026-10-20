"""The real ComfyUI server for the generation tests (marker "comfy").

One server for the folder (~15 s start), stopped at the end. Skipped when
ComfyUI isn't installed, or when another ComfyUI (the Desktop app) holds
the port: the pipeline never shares a server it didn't start.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from gen import comfy
from gen.core import ToolError
from gen.core import rules as gr


def pytest_collection_modifyitems(items):
    here = Path(__file__).parent
    for item in items:
        if here in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.comfy)


@pytest.fixture(scope="session")
def comfy_server():
    try:
        comfy.comfy_dir()
    except ToolError as exc:
        pytest.skip(str(exc))
    rules = gr.load().comfyui
    try:
        started = comfy.start(rules)
    except ToolError as exc:
        if "another ComfyUI" in str(exc):
            pytest.skip(str(exc))
        raise
    yield rules
    if started:
        comfy.stop()
