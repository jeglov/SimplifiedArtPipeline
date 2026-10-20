"""gen.core.repair: the bounded, deterministic repair loop. Pure Python, <1 s."""
from __future__ import annotations

import pytest

from gen.core import repair
from gen.core import rules as gr
from validate.core.conventions import ConventionError

POLICY = repair.Policy(max_attempts=3, seed_step=1000,
                       actions={"model.closed": "retry", "model.textures": "stop"}, default="stop")


def report(*failed, warnings=()):
    results = [{"id": c, "passed": False, "severity": "error"} for c in failed]
    results += [{"id": c, "passed": False, "severity": "warning"} for c in warnings]
    results.append({"id": "model.uv", "passed": True, "severity": "error"})
    return {"results": results}


def scripted(outcomes):
    """A fake stage: outcome per call, records the seeds it was given."""
    seeds = []

    def fn(seed):
        seeds.append(seed)
        code, rep = outcomes[len(seeds) - 1]
        return code, rep, "run%d/report.json" % len(seeds)
    return fn, seeds


def test_pass_first_time_is_one_attempt():
    fn, seeds = scripted([(0, report())])
    last, attempts = repair.run("model", 56, POLICY, fn)
    assert last.decision == repair.ACCEPT and seeds == [56] and len(attempts) == 1


def test_retry_with_the_next_seed_until_it_passes():
    fn, seeds = scripted([(1, report("model.closed")), (0, report())])
    last, attempts = repair.run("model", 56, POLICY, fn)
    assert last.decision == repair.ACCEPT and seeds == [56, 1056]
    assert [a.decision for a in attempts] == ["retry", "accept"]
    assert attempts[0].failed == ["model.closed"] and attempts[0].report == "run1/report.json"


def test_bounded_by_max_attempts():
    fn, seeds = scripted([(1, report("model.closed"))] * 5)
    last, attempts = repair.run("model", 56, POLICY, fn)
    assert last.decision == repair.STOP and len(attempts) == 3 and seeds == [56, 1056, 2056]
    assert "after 3 attempts" in last.reason


def test_policy_stop_hands_over_at_once():
    fn, seeds = scripted([(1, report("model.textures"))])
    last, _ = repair.run("model", 56, POLICY, fn)
    assert last.decision == repair.STOP and "human" in last.reason and seeds == [56]


def test_unknown_check_uses_the_default():
    assert repair.decide(1, ["model.new_thing"], 1, POLICY)[0] == repair.STOP


def test_the_first_blocking_failure_decides_and_warnings_are_ignored():
    rep = report("model.textures", "model.closed", warnings=["model.vertices"])
    assert repair.blocking_failures(rep) == ["model.textures", "model.closed"]
    assert repair.decide(1, repair.blocking_failures(rep), 1, POLICY)[0] == repair.STOP


def test_tool_error_never_retries():
    fn, seeds = scripted([(2, None)])
    last, _ = repair.run("model", 56, POLICY, fn)
    assert last.decision == repair.STOP and "tool error" in last.reason and seeds == [56]


def test_fail_without_a_blocking_check_stops():
    assert repair.decide(1, [], 1, POLICY)[0] == repair.STOP


def test_attempts_serialise():
    fn, _ = scripted([(1, report("model.closed")), (0, report())])
    _, attempts = repair.run("concept", 1, POLICY, fn)
    assert attempts[1].to_dict() == {"stage": "concept", "attempt": 2, "seed": 1001, "exit_code": 0,
                                     "report": "run2/report.json", "failed": [], "decision": "accept",
                                     "reason": "all blocking checks pass"}


# --- conventions ----------------------------------------------------------------------

def test_shipped_policy_covers_every_generation_check():
    r = gr.load().repair
    for cid in ("concept.background", "concept.coverage", "concept.margin", "concept.single_object",
                "model.closed", "model.manifold", "model.uv", "model.uv_overlap", "model.triangles",
                "model.textures"):
        assert cid in r.actions, cid
    assert r.default == "stop" and r.max_attempts >= 1


@pytest.mark.parametrize("repair_section, match", [
    ({"max_attempts": 0, "seed_step": 1, "default": "stop", "actions": {}}, "> 0"),
    ({"max_attempts": 3, "seed_step": 1, "default": "maybe", "actions": {}}, "retry or stop"),
    ({"max_attempts": 3, "seed_step": 1, "default": "stop", "actions": {"closed": "retry"}}, "check id"),
    ({"max_attempts": 3, "seed_step": 1, "default": "stop", "actions": {"model.closed": "again"}}, "retry or stop"),
])
def test_bad_policies_fail_at_load(repair_section, match):
    data = {"version": 1,
            "comfyui": {"version": "0.42.1", "commit": "a" * 40, "port": 8188, "startup_timeout_s": 1,
                        "run_timeout_s": 1},
            "models": {},
            "concept": dict(grid=64, mask_threshold=128, background_tolerance=28, background_uniformity_min=0.97,
                            coverage_min=0.06, coverage_max=0.7, margin_min=0.02, single_object_min=0.95,
                            speck_cells=4),
            "model": {"face_budget_share": 0.9, "remesh_resolution": 128, "uv_overlap_max_share": 0.005},
            "ingest": {"size_cm": {}},
            "repair": repair_section}
    with pytest.raises(ConventionError, match=match):
        gr.parse(data)
