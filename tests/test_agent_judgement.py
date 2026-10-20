"""Step 5.7: judgement tools. The pure records (<1 s) and, over MCP, images reaching the client's model,
verdicts recorded, and the prompt-change budget (#53) refusing before any GPU time."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent import cli as agent_cli
from agent.core import reviews
from agent.core import rules as ar

ROOT = Path(__file__).resolve().parents[1]
RULES = ar.load()


def test_rules_carry_the_judgement_budget():
    assert RULES.judgement.max_prompt_changes == 2 and RULES.judgement.max_image_bytes > 0


def test_a_review_needs_a_known_verdict_and_notes(tmp_path):
    with pytest.raises(ValueError, match="verdict"):
        reviews.record("x", "great", "nice", tmp_path)
    with pytest.raises(ValueError, match="say what you saw"):
        reviews.record("x", "reject", "  ", tmp_path)
    r = reviews.record("out/gen/runs/concept_zimage-1", "reject", "a flat icon, not a 3D object", tmp_path)
    assert reviews.list_reviews("out/gen/runs/concept_zimage-1", tmp_path) == [r]


def test_the_prompt_change_budget(tmp_path):
    root = "out/gen/runs/concept_zimage-aaa"
    assert reviews.budget_left(root, 2, tmp_path) == 2
    reviews.add_change(root, "a shaded 3D potion bottle", "was flat", "job-1", 2, tmp_path)
    reviews.add_change(root, "a glass potion bottle, 3/4 view", "still flat", "job-2", 2, tmp_path)
    assert reviews.budget_left(root, 2, tmp_path) == 0
    with pytest.raises(ValueError, match="a human decides"):
        reviews.add_change(root, "third try", "x", "job-3", 2, tmp_path)   # boundary: exactly max allowed


def test_a_retry_s_run_counts_against_its_original(tmp_path, monkeypatch):
    root = "out/gen/runs/concept_zimage-aaa"
    reviews.add_change(root, "s", "r", "job-1", 2, tmp_path)
    monkeypatch.setattr(reviews, "run_of_job", lambda job_id: "out/gen/runs/concept_zimage-bbb")
    # A retry of the retry still descends from the original: its budget is shared.
    assert reviews.root_of("out/gen/runs/concept_zimage-bbb", tmp_path) == root
    assert reviews.root_of("out/gen/runs/concept_zimage-ccc", tmp_path) == "out/gen/runs/concept_zimage-ccc"


# --- over MCP ---------------------------------------------------------------------------------

VENV_PY = agent_cli.venv_python(RULES)
pytestmark_agent = pytest.mark.skipif(not (VENV_PY.is_file() and agent_cli.status(RULES)["ok"]),
                                      reason="agent venv not set up: artpipe agent setup")


def calls(*steps):
    proc = subprocess.run([str(VENV_PY), str(ROOT / "tests" / "agent_client" / "calls.py"), json.dumps(list(steps))],
                          cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                          encoding="utf-8", timeout=900)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout)


def _concept_run():
    for d in sorted((ROOT / "out" / "gen" / "runs").glob("concept_zimage-*")):
        if (d / "concept.report.json").is_file() and (d / "image.png").is_file():
            return d.relative_to(ROOT).as_posix()
    return None


@pytest.mark.agent
@pytestmark_agent
@pytest.mark.skipif(_concept_run() is None, reason="no concept run on this machine")
def test_review_concept_shows_the_image_and_the_questions_then_records_the_verdict():
    run = _concept_run()
    view, verdict = calls(["review_concept", {"concept_run": run}],
                          ["record_review", {"target": run, "verdict": "good", "notes": "test: looks like the subject"}])
    assert view["types"][:2] == ["text", "image"] and view["mime"][0] == "image/png"
    assert "flat 2D drawing" in view["text"] and "concept.single_object" in view["text"]
    assert verdict["result"]["verdict"] == "good" and verdict["result"]["target"] == run


@pytest.mark.agent
@pytestmark_agent
@pytest.mark.parametrize("tool, args", [("review_image", {"path": "CLAUDE.md"}),
                                        ("review_image", {"path": "out/../docs/images/spotcap_grove_hero_endcard_sheet.png"}),
                                        ("review_concept", {"concept_run": "banners/kits/spotcap_grove"})])
def test_review_tools_refuse_paths_outside_their_folders(tool, args):
    (r,) = calls([tool, args])
    assert r["is_error"] and "refused" in r["text"]


@pytest.mark.agent
@pytestmark_agent
def test_review_image_of_a_kit_picture():
    (r,) = calls(["review_image", {"path": "banners/kits/spotcap_grove/hero.png"}])
    assert r["types"] == ["text", "image"]


@pytest.mark.agent
@pytestmark_agent
def test_retry_concept_is_refused_past_the_budget_before_any_gpu_time(tmp_path):
    root = "out/gen/runs/concept_zimage-budgettest%s" % tmp_path.name[-6:].replace("_", "")
    (ROOT / root).mkdir(parents=True, exist_ok=True)
    try:
        for i in range(RULES.judgement.max_prompt_changes):
            reviews.add_change(root, "s%d" % i, "r", "job-%d" % i, RULES.judgement.max_prompt_changes)
        (r,) = calls(["retry_concept", {"previous_run": root, "subject": "a better potion", "asset_class": "pickup",
                                        "style": "casual_toon", "reason": "still flat"}])
        assert r["is_error"] and "a human decides" in r["text"]
    finally:
        (ROOT / root).rmdir()
        lin = reviews.REVIEWS / "lineage" / ("%s.json" % reviews._key(root))
        if lin.exists():
            lin.unlink()


@pytest.mark.agent
@pytest.mark.render
@pytestmark_agent
def test_review_banner_returns_the_sheet(tmp_path):
    from agent.core import commands
    out = commands.run(["banners", "render", "spotcap_grove", "--template", "tests/fixtures/banners/short_endcard.yaml",
                        "--out", "out/agent/test_banners"], 600)
    folder = "out/agent/test_banners/spotcap_grove/short_endcard/ua_video_9x16/en"
    assert out.exit_code == 1
    (r,) = calls(["review_banner", {"folder": folder}])
    assert r["types"] == ["text", "image"] and "does the hero stand out" in r["text"].lower()
    assert "banner.duration" in r["text"]
