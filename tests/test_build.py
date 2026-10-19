"""pipeline.build verdict logic, without Maya or Unity."""
from __future__ import annotations

import pytest

from pipeline.build import verdict


def stage(code):
    return {"exit_code": code}


@pytest.mark.parametrize("maya, unity, expected", [
    (0, 0, (0, "pass", None)),
    (1, None, (1, "fail", "maya")),        # Maya couldn't fix it: Unity never runs
    (2, None, (2, "error", "maya")),
    (0, 1, (1, "fail", "unity")),          # e.g. GPU vertex budget after import
    (0, 2, (2, "error", "unity")),         # e.g. licence, locked project
])
def test_worst_stage_wins_and_names_where_it_stopped(maya, unity, expected):
    v = verdict({"maya": stage(maya), "unity": None if unity is None else stage(unity)})
    assert (v["exit_code"], v["status"], v["stopped_at"]) == expected


def test_a_skipped_stage_after_a_pass_is_never_a_pass():
    # Boundary: Maya passed but Unity didn't run (e.g. nothing exported).
    v = verdict({"maya": stage(0), "unity": None})
    assert v["exit_code"] == 2 and v["status"] == "error"
