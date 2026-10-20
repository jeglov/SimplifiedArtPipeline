"""gen.core.repair: the bounded, deterministic repair loop. Pure Python, <1 s."""
from __future__ import annotations

import json
import re

import jsonschema
import pytest

from gen import cli
from gen.core import ToolError
from gen.core import repair
from gen.core import rules as gr
from validate.core.conventions import DEFAULT_DIR, ConventionError

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
        n = len(seeds)
        return repair.Outcome(code, rep, "run%d/report.json" % n if rep else None, "run%d" % n, 60.0)
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
                                     "reason": "all blocking checks pass", "run": "run2", "seconds": 60.0,
                                     "reused": False, "message": ""}


# --- the record -----------------------------------------------------------------------

RECORD_SCHEMA = jsonschema.Draft202012Validator(
    json.loads((DEFAULT_DIR / "gen_repair.schema.json").read_text(encoding="utf-8")))
REQUEST = {"image_sha256": "a" * 64, "asset_class": "pickup", "base_seed": 56}


def test_record_matches_its_schema_for_every_ending():
    for outcomes in ([(0, report())], [(1, report("model.closed"))] * 3, [(1, report("model.textures"))],
                     [(2, None)]):
        fn, _ = scripted(outcomes)
        last, attempts = repair.run("model", 56, POLICY, fn)
        rec = repair.record("model", REQUEST, POLICY, last, attempts)
        errors = [e.message for e in RECORD_SCHEMA.iter_errors(rec)]
        assert not errors, errors


def test_record_exit_codes_and_accepted_run():
    cases = [([(0, report())], 0, "run1"), ([(1, report("model.closed")), (0, report())], 0, "run2"),
             ([(1, report("model.closed"))] * 3, 1, None), ([(1, report("model.textures"))], 1, None),
             ([(2, None)], 2, None)]
    for outcomes, code, accepted in cases:
        fn, _ = scripted(outcomes)
        rec = repair.record("model", REQUEST, POLICY, *repair.run("model", 56, POLICY, fn))
        assert (rec["exit_code"], rec["accepted_run"]) == (code, accepted), outcomes


def test_gpu_seconds_count_only_attempts_that_ran_now():
    outs = iter([repair.Outcome(1, report("model.closed"), "r1", "run1", 240.0, reused=True),
                 repair.Outcome(0, report(), "r2", "run2", 200.5)])
    last, attempts = repair.run("model", 56, POLICY, lambda seed: next(outs))
    s = repair.record("model", REQUEST, POLICY, last, attempts)["summary"]
    assert s == {"attempts": 2, "reused": 1, "gpu_seconds": 200.5}


def test_repair_id_is_stable_and_follows_the_request():
    a = repair.repair_id("model", REQUEST)
    assert a == repair.repair_id("model", dict(reversed(list(REQUEST.items()))))
    assert a != repair.repair_id("model", dict(REQUEST, base_seed=57))
    assert a != repair.repair_id("concept", REQUEST)
    assert re.match(r"^model-[0-9a-f]{12}$", a)


# --- gen.cli: the loop around a real stage function --------------------------------------

def stage_report(*failed):
    return {"status": "fail" if failed else "pass",
            "results": [{"id": c, "passed": False, "severity": "error"} for c in failed]}


class FakeStage:
    """Stands in for gen.cli.model / concept: writes a run folder per seed."""

    def __init__(self, tmp_path, verdicts, report_name="model.report.json"):
        self.tmp, self.verdicts, self.name, self.seeds, self.keep = tmp_path, verdicts, report_name, [], []

    def __call__(self, *args):
        seed, out_dir, keep = args[-4], args[-3], args[-2]
        self.seeds.append(seed)
        self.keep.append(keep)
        verdict = self.verdicts[len(self.seeds) - 1]
        if isinstance(verdict, Exception):
            raise verdict
        run_dir = out_dir / ("run-%d" % seed)
        run_dir.mkdir(parents=True)
        (run_dir / self.name).write_text(json.dumps(stage_report(*verdict)), encoding="utf-8")
        (run_dir / "manifest.json").write_text(json.dumps({"execution": {"seconds": 100.0}}), encoding="utf-8")
        return (1 if verdict else 0), ("FAIL" if verdict else "PASS"), run_dir


@pytest.fixture
def no_server(monkeypatch, tmp_path):
    calls = {"stop": 0}
    # The real out/gen/comfy may hold a live, kept server (the generation tests start one):
    # the server-sharing state of these fakes lives in a temp folder.
    monkeypatch.setattr(cli.comfy, "WORK", tmp_path / "comfy")
    monkeypatch.setattr(cli.comfy.Client, "stats", lambda self: None)

    def stop(*a, **k):
        calls["stop"] += 1
        return True
    monkeypatch.setattr(cli.comfy, "stop", stop)
    return calls


def write_image(tmp_path):
    img = tmp_path / "concept.png"
    img.write_bytes(b"\x89PNG fake")
    return img


def test_model_repair_retries_by_seed_and_writes_the_record(monkeypatch, tmp_path, no_server):
    stage = FakeStage(tmp_path, [["model.uv_overlap"], ["model.manifold"], []])
    monkeypatch.setattr(cli, "model", stage)
    code, message, rec = cli.model_repaired(str(write_image(tmp_path)), "pickup", 56, tmp_path / "runs")
    # The attempts don't keep the server themselves: the repair loop's session holds it (#45).
    assert code == 0 and stage.seeds == [56, 1056, 2056] and not any(stage.keep)
    assert message.startswith("PASS after 3 attempt(s)") and rec["accepted_run"].endswith("run-2056")
    (path,) = (tmp_path / "repairs").glob("model-*.json")
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == rec and not list(RECORD_SCHEMA.iter_errors(on_disk))
    assert [a["failed"] for a in rec["attempts"]] == [["model.uv_overlap"], ["model.manifold"], []]
    assert rec["summary"]["gpu_seconds"] == 300.0
    assert no_server["stop"] == 1  # it wasn't up before: the loop stops what the attempts started


def test_model_repair_hands_over_on_a_stop_check(monkeypatch, tmp_path, no_server):
    stage = FakeStage(tmp_path, [["model.textures"]])
    monkeypatch.setattr(cli, "model", stage)
    code, message, rec = cli.model_repaired(str(write_image(tmp_path)), "pickup", 56, tmp_path / "runs")
    assert code == 1 and stage.seeds == [56] and message.startswith("HANDED OVER after 1 attempt(s)")


def test_a_tool_error_in_a_stage_is_recorded_not_raised(monkeypatch, tmp_path, no_server):
    stage = FakeStage(tmp_path, [ToolError("the concept image in X failed its checks")])
    monkeypatch.setattr(cli, "model", stage)
    code, message, rec = cli.model_repaired(str(write_image(tmp_path)), "pickup", 56, tmp_path / "runs")
    assert code == 2 and message.startswith("ERROR") and stage.seeds == [56]
    assert rec["attempts"][0]["message"] == "the concept image in X failed its checks"
    assert rec["attempts"][0]["report"] is None


def test_a_server_that_was_already_up_is_left_running(monkeypatch, tmp_path, no_server):
    monkeypatch.setattr(cli.comfy.Client, "stats", lambda self: {"system": {}})
    monkeypatch.setattr(cli, "model", FakeStage(tmp_path, [[]]))
    cli.model_repaired(str(write_image(tmp_path)), "pickup", 56, tmp_path / "runs")
    assert no_server["stop"] == 0


def test_keep_server_leaves_it_running(monkeypatch, tmp_path, no_server):
    monkeypatch.setattr(cli, "model", FakeStage(tmp_path, [[]]))
    cli.model_repaired(str(write_image(tmp_path)), "pickup", 56, tmp_path / "runs", keep_server=True)
    assert no_server["stop"] == 0


def test_the_server_is_stopped_even_when_a_stage_crashes(monkeypatch, tmp_path, no_server):
    monkeypatch.setattr(cli, "model", FakeStage(tmp_path, [RuntimeError("bug")]))
    with pytest.raises(RuntimeError):
        cli.model_repaired(str(write_image(tmp_path)), "pickup", 56, tmp_path / "runs")
    assert no_server["stop"] == 1


def test_concept_repair_uses_the_concept_report(monkeypatch, tmp_path, no_server):
    stage = FakeStage(tmp_path, [["concept.margin"], []], "concept.report.json")
    monkeypatch.setattr(cli, "concept", stage)
    code, _, rec = cli.concept_repaired("a coin", "pickup", "casual_toon", 1, tmp_path / "runs")
    assert code == 0 and stage.seeds == [1, 1001] and rec["stage"] == "concept"
    assert rec["request"] == {"subject": "a coin", "asset_class": "pickup", "style": "casual_toon",
                              "base_seed": 1}


def test_cli_flag_selects_the_loop(monkeypatch, tmp_path, no_server):
    monkeypatch.setattr(cli, "model", FakeStage(tmp_path, [["model.closed"], []]))
    assert cli.main(["model", str(write_image(tmp_path)), "--class", "pickup", "--repair",
                     "--out", str(tmp_path / "runs")]) == 0
    assert len(list((tmp_path / "repairs").glob("*.json"))) == 1


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


def test_tool_error_messages_in_the_record_carry_no_machine_paths(monkeypatch, tmp_path, no_server):
    missing = str(cli.comfy.ROOT / "models" / "x.safetensors")
    monkeypatch.setattr(cli, "model", FakeStage(tmp_path, [ToolError("model x missing: %s" % missing)]))
    _, _, rec = cli.model_repaired(str(write_image(tmp_path)), "pickup", 56, tmp_path / "runs")
    message = rec["attempts"][0]["message"]
    assert str(cli.comfy.ROOT) not in message and message.startswith("model x missing: <repo>")
