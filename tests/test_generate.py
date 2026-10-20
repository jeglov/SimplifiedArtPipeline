"""pipeline.generate: prompt -> prefab orchestration, without ComfyUI, Maya or Unity.

Every stage is replaced by a fake that writes what the real one writes (repair
records, manifests, the ingest record), so the report contract and the stop
logic are tested in <1 s. The real chain runs in docs/generation.md's demo.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from gen.core import ToolError
from gen.core import repair
from pipeline import generate as g
from validate.core import conventions as conv

SCHEMA = jsonschema.Draft202012Validator(
    json.loads((conv.DEFAULT_DIR / "build_report.schema.json").read_text(encoding="utf-8")))
POLICY = repair.Policy(3, 1000, {"model.closed": "retry", "concept.margin": "retry"}, "stop")
MODELS = [{"id": "trellis2_int8", "sha256": "b" * 64, "licence": "MIT", "folder": "x", "file": "y", "source": "z"},
          {"id": "dino_v3_vitl", "sha256": "c" * 64, "licence": "DINOv3-License", "folder": "x", "file": "y",
           "source": "z"}]
ENV = {"comfyui_version": "0.42.1", "comfyui_commit": "a" * 40, "python": "3.13.12", "torch": "2.12.1",
       "gpu": "RTX 3070 Laptop", "vram_mb": 8191, "driver": "595.97", "argv": ["<path>"]}


class Fakes:
    def __init__(self, tmp_path, concept=((0, []),), model=((0, []),), ingest_error=None, build_code=0):
        self.tmp, self.concept, self.model = tmp_path, list(concept), list(model)
        self.ingest_error, self.build_code, self.calls = ingest_error, build_code, []
        self.runs = tmp_path / "gen" / "runs"

    def _stage(self, stage, outcomes, base_seed, request):
        attempts_out = []
        for n, (code, failed) in enumerate(outcomes):
            run = self.runs / ("%s-%d" % (stage, n))
            run.mkdir(parents=True, exist_ok=True)
            (run / "manifest.json").write_text(json.dumps({"models": MODELS, "environment": ENV}), encoding="utf-8")
            report = {"results": [{"id": c, "passed": False, "severity": "error"} for c in failed]}
            attempts_out.append(repair.Outcome(code, report, str(run / "r.json"), str(run), 100.0))
        it = iter(attempts_out)
        last, attempts = repair.run(stage, base_seed, POLICY, lambda seed: next(it))
        rec = repair.record(stage, request, POLICY, last, attempts)
        path = self.runs.parent / "repairs" / (rec["repair_id"] + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rec), encoding="utf-8")
        return rec["exit_code"], "msg", rec

    def install(self, monkeypatch):
        def concept_repaired(subject, cls, style, seed, runs, keep, env):
            self.calls.append(("concept", seed, keep))
            return self._stage("concept", self.concept, seed, {"subject": subject, "base_seed": seed})

        def model_repaired(source, cls, seed, runs, keep, env):
            self.calls.append(("model", seed, keep, source))
            return self._stage("model", self.model, seed, {"image": source, "base_seed": seed})

        def ingest(model_run, cls, name, out_dir):
            self.calls.append(("ingest", model_run, name))
            if self.ingest_error:
                raise self.ingest_error
            d = out_dir / name
            d.mkdir(parents=True)
            for f in ("SM_%s.ma" % name, "T_%s_D.png" % name, "T_%s_N.png" % name, "ingest.json"):
                (d / f).write_text(f, encoding="utf-8")
            return 0, "OK", d

        def build(scene, cls, textures, out):
            self.calls.append(("build", scene.name, [t.name for t in textures]))
            unity = {"exit_code": self.build_code, "status": "pass" if self.build_code == 0 else "fail",
                     "report": "u.json", "asset": "Assets/Art/pickup/SM_Coin.fbx",
                     "prefab": "Assets/Art/pickup/SM_Coin.prefab",
                     "textures": [], "materials": ["Assets/Art/Materials/M_Coin.mat"]}
            return {"schema_version": "1.1", "tool": "artpipe.build 1.0",
                    "source": {"path": str(scene), "sha256": "d" * 64}, "asset_class": cls, "textures": [],
                    "status": unity["status"], "stopped_at": None if self.build_code == 0 else "unity",
                    "stages": {"maya": {"exit_code": 0, "status": "pass", "report": "m.json", "fixes_applied": 0,
                                        "fbx": "x.fbx", "fbx_sha256": "e" * 64, "fbx_report": "x.json"},
                               "unity": unity},
                    "exit_code": self.build_code, "generation": None}

        monkeypatch.setattr(g.gen, "concept_repaired", concept_repaired)
        monkeypatch.setattr(g.gen, "model_repaired", model_repaired)
        monkeypatch.setattr(g.gen, "ingest", ingest)
        monkeypatch.setattr(g.bld, "build", build)
        self.stops = []
        monkeypatch.setattr(g.comfy.Client, "stats", lambda c: None)
        monkeypatch.setattr(g.comfy, "stop", lambda *a, **k: self.stops.append(1))
        return self


def run(tmp_path, fakes, name="Coin", **kw):
    return g.generate("a golden coin", "pickup", "casual_toon", name, runs=fakes.runs,
                      assets=tmp_path / "assets", out=tmp_path / "build", **kw)


def valid(report):
    errors = [e.message for e in SCHEMA.iter_errors(report)]
    assert not errors, errors
    return report


def test_full_chain_carries_provenance_into_the_build_report(monkeypatch, tmp_path):
    f = Fakes(tmp_path, model=[(1, ["model.closed"]), (0, [])]).install(monkeypatch)
    r = valid(run(tmp_path, f))
    assert r["exit_code"] == 0 and r["stopped_at"] is None
    gen = r["generation"]
    assert gen["stages"]["model"]["seeds"] == [56, 1056]
    assert gen["stages"]["model"]["failed"] == [["model.closed"], []]
    assert gen["stages"]["concept"]["seeds"] == [1]
    assert gen["gpu_seconds"] == 300.0  # 1 concept + 2 model attempts, 100 s each
    assert [m["id"] for m in gen["models"]] == ["dino_v3_vitl", "trellis2_int8"]
    assert {m["licence"] for m in gen["models"]} == {"MIT", "DINOv3-License"}
    assert gen["environment"]["gpu"] == "RTX 3070 Laptop" and "argv" not in gen["environment"]
    assert gen["stages"]["ingest"]["record"]["path"].endswith("ingest.json")
    # the model stage gets the accepted concept run; build gets both textures
    assert f.calls[1][3].endswith("concept-0")
    assert f.calls[-1] == ("build", "SM_Coin.ma", ["T_Coin_D.png", "T_Coin_N.png"])


def test_the_server_stays_up_from_concept_to_model_and_is_stopped_once(monkeypatch, tmp_path):
    f = Fakes(tmp_path).install(monkeypatch)
    run(tmp_path, f)
    assert [c[2] for c in f.calls if c[0] in ("concept", "model")] == [True, True]
    assert f.stops == [1]


def test_keep_server_or_a_running_server_is_left_alone(monkeypatch, tmp_path):
    f = Fakes(tmp_path).install(monkeypatch)
    run(tmp_path, f, keep_server=True)
    monkeypatch.setattr(g.comfy.Client, "stats", lambda c: {"system": {}})
    run(tmp_path, f, name="Coin2")
    assert f.stops == []


def test_concept_handed_over_stops_before_the_gpu_heavy_stage(monkeypatch, tmp_path):
    f = Fakes(tmp_path, concept=[(1, ["concept.margin"])] * 3).install(monkeypatch)
    r = valid(run(tmp_path, f))
    assert (r["exit_code"], r["status"], r["stopped_at"]) == (1, "fail", "concept")
    assert [c[0] for c in f.calls] == ["concept"] and r["source"] is None
    assert r["generation"]["stages"]["model"] is None and r["generation"]["stages"]["ingest"] is None


def test_model_tool_error_is_exit_2_and_never_reaches_maya(monkeypatch, tmp_path):
    f = Fakes(tmp_path, model=[(2, [])]).install(monkeypatch)
    r = valid(run(tmp_path, f))
    assert (r["exit_code"], r["status"], r["stopped_at"]) == (2, "error", "model")
    assert "ingest" not in [c[0] for c in f.calls]


def test_a_model_handed_over_after_its_retries_never_reaches_ingest(monkeypatch, tmp_path):
    # The crate's case: every attempt fails a retry check. Found by the step 3.8 mutation pass.
    f = Fakes(tmp_path, model=[(1, ["model.closed"])] * 3).install(monkeypatch)
    r = valid(run(tmp_path, f))
    assert (r["exit_code"], r["status"], r["stopped_at"]) == (1, "fail", "model")
    assert [c[0] for c in f.calls] == ["concept", "model"]
    assert r["generation"]["stages"]["model"]["seeds"] == [56, 1056, 2056]


def test_ingest_error_is_reported_with_the_message(monkeypatch, tmp_path):
    f = Fakes(tmp_path, ingest_error=ToolError("size 99 cm is outside pickup's 5-50 cm")).install(monkeypatch)
    r = valid(run(tmp_path, f))
    assert (r["exit_code"], r["stopped_at"]) == (2, "ingest")
    assert r["generation"]["stages"]["ingest"] == {"exit_code": 2, "record": None,
                                                   "message": "size 99 cm is outside pickup's 5-50 cm"}


def test_a_unity_failure_keeps_the_generation_record(monkeypatch, tmp_path):
    f = Fakes(tmp_path, build_code=1).install(monkeypatch)
    r = valid(run(tmp_path, f))
    assert r["exit_code"] == 1 and r["stopped_at"] == "unity" and r["generation"]["stages"]["model"]


@pytest.mark.parametrize("kw, match", [
    ({"name": "coin"}, "--name"),             # SM_coin breaks the naming convention
    ({"style": "photoreal"}, "unknown style"),
])
def test_bad_requests_are_refused_before_any_gpu_time(monkeypatch, tmp_path, kw, match):
    f = Fakes(tmp_path).install(monkeypatch)
    args = dict(subject="a coin", asset_class="pickup", style="casual_toon", name="Coin")
    args.update(kw)
    with pytest.raises((ToolError, conv.ConventionError), match=match):
        g.generate(args["subject"], args["asset_class"], args["style"], args["name"], runs=f.runs,
                   assets=tmp_path / "assets", out=tmp_path / "build")
    assert f.calls == []


def test_main_writes_the_report_where_build_would(monkeypatch, tmp_path, capsys):
    Fakes(tmp_path).install(monkeypatch)
    monkeypatch.setattr(g.gen, "RUNS", tmp_path / "gen" / "runs")
    real = g.generate
    monkeypatch.setattr(g, "generate", lambda *a, **k: real(*a, runs=tmp_path / "gen" / "runs",
                                                             assets=tmp_path / "assets", **k))
    code = g.main(["a golden coin", "--class", "pickup", "--style", "casual_toon", "--name", "Coin",
                   "--out", str(tmp_path / "build")])
    assert code == 0
    report = json.loads((tmp_path / "build" / "SM_Coin" / "build.report.json").read_text(encoding="utf-8"))
    valid(report)
    assert "PASS" in capsys.readouterr().out

