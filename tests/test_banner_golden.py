"""The golden banner: the shipped kit in the 1 s template against its recorded snapshot. Marker render, ~25 s.

Re-record with `artpipe py tests/fixtures/banners/golden_record.py` after a deliberate change.
Three levels, strictest last:
  * layout: the layout engine's text measure and the fonts it used, exactly;
  * pixels: a 12x21 colour grid of every decoded key frame, within GRID_TOLERANCE per channel
    (what survives another machine: backlog #61);
  * bytes: the MP4's sha256 (byte-identical on this machine; another CPU may differ, #61).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from banners import tools
from banners.core import ToolError
from banners.core import rules as br

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "banners"))
import golden_record  # noqa: E402

RULES = br.load()
try:
    tools.require(RULES)
    MISSING = None
except ToolError as exc:
    MISSING = str(exc)

pytestmark = [pytest.mark.render, pytest.mark.skipif(MISSING is not None, reason=MISSING or "")]
GRID_TOLERANCE = 6
GOLDEN = json.loads(golden_record.GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def snap(tmp_path_factory):
    return golden_record.snapshot(golden_record.render(tmp_path_factory.mktemp("golden")))


def test_layout_and_fonts_match_the_golden(snap):
    assert snap["key_frames"] == GOLDEN["key_frames"]
    assert snap["measure"] == GOLDEN["measure"]
    assert snap["fonts"] == GOLDEN["fonts"]


def test_decoded_frames_match_the_golden_within_tolerance(snap):
    worst = 0
    for n, cells in GOLDEN["grids"].items():
        for a, b in zip(cells, snap["grids"][n]):
            worst = max(worst, max(abs(x - y) for x, y in zip(a, b)))
    assert worst <= GRID_TOLERANCE, "a key frame drifted by %d levels in some cell" % worst


def test_the_mp4_is_byte_identical_to_the_golden_on_this_machine(snap):
    assert snap["mp4_sha256"] == GOLDEN["mp4_sha256"]
