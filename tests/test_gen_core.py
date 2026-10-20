"""gen.core: rules, workflows, ComfyUI replies and manifests, without ComfyUI.

Pure Python, runs anywhere in <1 s. ComfyUI's answers come from real
recordings (tests/fixtures/comfy/, re-recorded by comfy_record.py), not
hand-written JSON. The live server runs in tests/generation/.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from gen.core import ToolError
from gen.core import history as hist
from gen.core import manifest as mf
from gen.core import rules as gr
from gen.core import workflow as wfl
from validate.core.conventions import DEFAULT_DIR, ConventionError

FIX = Path(__file__).resolve().parent / "fixtures" / "comfy"
SHA = "ab" * 32
COMMIT = "63be2f9b82ad0dac88a7ceaa28a9cf3154125a18"


def recorded(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def rules_data(**models):
    return {"version": 1,
            "comfyui": {"version": "0.42.1", "commit": COMMIT, "port": 8188,
                        "startup_timeout_s": 180, "run_timeout_s": 1800},
            "models": models,
            "concept": dict(CONCEPT),
            "model": {"face_budget_share": 0.9, "remesh_resolution": 128, "uv_overlap_max_share": 0.005},
            "ingest": {"size_cm": {"pickup": 40}},
            "repair": {"max_attempts": 3, "seed_step": 1000, "default": "stop", "actions": {}}}


CONCEPT = {"grid": 64, "mask_threshold": 128, "background_tolerance": 28, "background_uniformity_min": 0.97,
           "coverage_min": 0.06, "coverage_max": 0.7, "margin_min": 0.02,
           "single_object_min": 0.95, "speck_cells": 4}


def pin(file="z.safetensors", sha=SHA):
    return {"folder": "diffusion_models", "file": file, "sha256": sha,
            "licence": "Apache-2.0", "source": "https://example.com/z"}


RULES = gr.parse(rules_data(zimg=pin()))


def wf_data(**over):
    data = {
        "id": "demo", "description": "d", "models": ["zimg"],
        "params": {"seed": {"node": "3", "input": "seed", "type": "int", "default": 1},
                   "cfg": {"node": "3", "input": "cfg", "type": "float", "default": 1.0},
                   "prompt": {"node": "6", "input": "text", "type": "str", "default": "a gem"}},
        "outputs": {"image": "9"},
        "graph": {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "z.safetensors"}},
                  "3": {"class_type": "KSampler", "inputs": {"seed": 0, "cfg": 2.0, "model": ["1", 0]}},
                  "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
                  "9": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": "x"}}},
    }
    data.update(over)
    return data


def parse_wf(data, name="demo.json", rules=RULES):
    return wfl.parse(data, Path(name), SHA, rules)


# --- conventions/generation.yaml ------------------------------------------------

def test_shipped_rules_load_and_pin_the_probed_build():
    r = gr.load()
    assert (r.comfyui.version, r.comfyui.commit) == ("0.42.1", COMMIT)


@pytest.mark.parametrize("mutate, match", [
    (lambda d: d["comfyui"].update(commit="63be2f9"), "40-char"),
    (lambda d: d["comfyui"].update(port=65536), "<= 65535"),
    (lambda d: d["comfyui"].update(run_timeout_s=0), "> 0"),
    (lambda d: d["comfyui"].update(extra=1), "unknown key"),
    (lambda d: d["models"].update(zimg=pin(sha="AB" * 32)), "lowercase hex"),
    (lambda d: d["models"].update(zimg=pin(file="sub/z.safetensors")), "plain names"),
    (lambda d: d["models"].update(Zimg=pin()), "snake_case"),
    (lambda d: d["models"].update(zimg=dict(pin(), licence=None)), "licence"),
])
def test_bad_rules_fail_at_load(mutate, match):
    data = rules_data(zimg=pin())
    mutate(data)
    with pytest.raises(ConventionError, match=match):
        gr.parse(data)


def test_port_boundary_65535_is_valid():
    data = rules_data()
    data["comfyui"]["port"] = 65535
    assert gr.parse(data).comfyui.port == 65535


# --- workflow files ---------------------------------------------------------------

def test_shipped_workflows_all_load():
    files = sorted(wfl.WORKFLOW_DIR.glob("*.json"))
    assert files
    for f in files:
        assert wfl.load(f, gr.load()).id == f.stem


def test_workflow_by_id_resolves_to_the_workflow_folder():
    assert wfl.load(Path("smoke_empty_image"), gr.load()).path.parent == wfl.WORKFLOW_DIR


@pytest.mark.parametrize("over, match", [
    ({"id": "other"}, "equal the file name"),
    ({"graph": {"1": {"inputs": {}}}}, "Export \\(API\\)"),  # UI-format graphs lack class_type
    ({"models": ["nope"]}, "not pinned"),
    ({"models": []}, "not declared"),  # graph still loads z.safetensors
    ({"outputs": {"image": "3"}}, "filename_prefix"),
    ({"outputs": {}}, "at least one"),
    ({"extra": 1}, "unknown keys"),
])
def test_bad_workflows_are_refused(over, match):
    with pytest.raises(ToolError, match=match):
        parse_wf(wf_data(**over))


@pytest.mark.parametrize("spec, match", [
    ({"node": "99", "input": "seed", "type": "int", "default": 1}, "not in the graph"),
    ({"node": "3", "input": "nope", "type": "int", "default": 1}, "not in the graph"),
    ({"node": "3", "input": "seed", "type": "bool", "default": 1}, "type must be"),
    ({"node": "3", "input": "seed", "type": "int", "default": True}, "not a int"),
    ({"node": "3", "input": "seed", "type": "int", "default": "1"}, "not a int"),
    ({"node": "3", "input": "seed", "type": "int"}, "keys"),
])
def test_bad_params_are_refused(spec, match):
    data = wf_data()
    data["params"]["seed"] = spec
    with pytest.raises(ToolError, match=match):
        parse_wf(data)


def test_undeclared_model_file_anywhere_in_the_graph_is_refused():
    data = wf_data()
    data["graph"]["7"] = {"class_type": "LoraLoader", "inputs": {"lora_name": "styles\\toon.SafeTensors"}}
    with pytest.raises(ToolError, match="toon.SafeTensors"):
        parse_wf(data)


def test_model_files_are_matched_by_extension_case_insensitively():
    graph = {"1": {"class_type": "X", "inputs": {"a": "sub/m.GGUF", "b": "photo.png", "c": 3}}}
    assert wfl.model_files(graph) == ["m.GGUF"]


def test_overrides_are_typed_and_unknown_names_fail():
    wf = parse_wf(wf_data())
    assert wfl.parse_overrides(["seed=7", "cfg=2", "prompt=a=b"], wf) == {"seed": 7, "cfg": 2.0, "prompt": "a=b"}
    with pytest.raises(ToolError, match="has params"):
        wfl.parse_overrides(["sed=7"], wf)
    with pytest.raises(ToolError, match="not a int"):
        wfl.parse_overrides(["seed=1.5"], wf)
    with pytest.raises(ToolError, match="name=value"):
        wfl.parse_overrides(["seed"], wf)


def test_resolve_makes_every_param_explicit_and_float_stays_float():
    wf = parse_wf(wf_data())
    values = wfl.resolve_params(wf, {"cfg": 3})
    assert values == {"cfg": 3.0, "prompt": "a gem", "seed": 1}
    assert isinstance(values["cfg"], float)


def test_fill_applies_params_and_never_mutates_the_workflow():
    wf = parse_wf(wf_data())
    before = copy.deepcopy(wf.graph)
    graph = wfl.fill(wf, {"seed": 42, "cfg": 1.0, "prompt": "p"})
    assert graph["3"]["inputs"]["seed"] == 42 and graph["6"]["inputs"]["text"] == "p"
    assert wf.graph == before


def _rid(wf, seed=1, model_sha=SHA, commit=COMMIT):
    graph = wfl.fill(wf, wfl.resolve_params(wf, {"seed": seed}))
    return wfl.run_id(wf, graph, {"zimg": model_sha}, commit)


def test_run_id_is_stable_and_changes_with_everything_that_changes_the_output():
    wf = parse_wf(wf_data())
    base = _rid(wf)
    assert base == _rid(wf) and base.startswith("demo-") and len(base) == len("demo-") + 12
    assert _rid(wf, seed=2) != base
    assert _rid(wf, model_sha="cd" * 32) != base
    assert _rid(wf, commit="0" * 40) != base


def test_run_id_ignores_output_prefixes_and_dict_order():
    wf = parse_wf(wf_data())
    graph = wfl.fill(wf, wfl.resolve_params(wf, {}))
    reordered = dict(reversed(list(graph.items())))
    renamed = copy.deepcopy(graph)
    renamed["9"]["inputs"]["filename_prefix"] = "anything"
    rid = wfl.run_id(wf, graph, {"zimg": SHA}, COMMIT)
    assert wfl.run_id(wf, reordered, {"zimg": SHA}, COMMIT) == rid
    # The prefix in the source graph is overwritten per run, but it's still
    # part of the graph: the id only ignores prefixes added by with_prefixes.
    assert wfl.run_id(wf, renamed, {"zimg": SHA}, COMMIT) != rid


def test_with_prefixes_points_every_output_into_the_run_folder():
    wf = parse_wf(wf_data())
    graph = wfl.with_prefixes(wf, wf.graph, "demo-0123456789ab")
    assert graph["9"]["inputs"]["filename_prefix"] == "artpipe/demo-0123456789ab/image"
    assert wf.graph["9"]["inputs"]["filename_prefix"] == "x"


# --- ComfyUI replies (recorded) -----------------------------------------------------

def test_recorded_success():
    rec = recorded("history_success.json")
    ex = hist.parse_history(rec["entry"], rec["graph"])
    assert ex.ok and ex.error is None
    assert ex.executed_nodes == ["1", "2"] and ex.cached_nodes == []
    assert [(f.node, f.type) for f in ex.files] == [("2", "output")]
    assert ex.files[0].filename.endswith(".png") and ex.seconds is not None and ex.seconds >= 0


def test_recorded_rerun_is_fully_cached_and_points_at_the_old_file():
    first = recorded("history_success.json")
    again = recorded("history_cached.json")
    ex = hist.parse_history(again["entry"], again["graph"])
    assert ex.ok and ex.executed_nodes == [] and ex.cached_nodes == ["1", "2"]
    old = hist.parse_history(first["entry"], first["graph"]).files
    assert ex.files == old


def test_recorded_runtime_error_names_the_node_and_stops_there():
    rec = recorded("history_execution_error.json")
    ex = hist.parse_history(rec["entry"], rec["graph"])
    assert not ex.ok and ex.files == []
    assert ex.error["type"] == "execution_error"
    assert (ex.error["node"], ex.error["node_type"]) == ("1", "LoadImage")
    assert ex.error["exception_type"] == "PIL.UnidentifiedImageError"
    assert ex.executed_nodes == ["1"]  # SaveImage after the failing node never ran


def test_failure_without_detail_is_still_an_error():
    entry = {"status": {"status_str": "error", "completed": False, "messages": []}, "outputs": {}}
    ex = hist.parse_history(entry, {"1": {}})
    assert not ex.ok and ex.error["type"] == "error"


def test_completed_flag_alone_is_not_success():
    entry = {"status": {"status_str": "error", "completed": True, "messages": []}, "outputs": {}}
    assert not hist.parse_history(entry, {}).ok


def test_unexpected_history_shape_is_a_tool_error():
    with pytest.raises(ToolError):
        hist.parse_history({"outputs": {}}, {})


@pytest.mark.parametrize("case, expected", [
    ("missing_link", "prompt_outputs_failed_validation"),
    ("value_below_min", "node 1 (EmptyImage) width: value_smaller_than_min"),
    ("unknown_node", "missing_node_type: Node 'NoSuchNode' not found"),
])
def test_recorded_refusals_become_one_readable_line(case, expected):
    r = recorded("prompt_refusals.json")[case]
    assert r["status"] == 400
    msg = hist.prompt_error(r["status"], r["body"])
    assert expected in msg and "\n" not in msg


def test_refusal_with_a_non_json_body_still_reports():
    assert "HTTP 500" in hist.prompt_error(500, "Internal Server Error")


# --- manifest ---------------------------------------------------------------------

def smoke_wf():
    return wfl.load(Path("smoke_empty_image"), gr.load())


def out_file(node, name, kind="output"):
    return hist.OutputFile(node, name, "artpipe/x", kind)


def test_output_names_single_batch_and_ignored_files():
    wf = smoke_wf()
    assert [(n, l) for n, l, _ in mf.output_names([out_file("2", "a_00001_.png")], wf)] == [("image", "image.png")]
    batch = [out_file("2", "a_00001_.png"), out_file("2", "a_00002_.png")]
    assert [l for _, l, _ in mf.output_names(batch, wf)] == ["image_1.png", "image_2.png"]
    noise = [out_file("1", "preview.png"), out_file("2", "t.png", kind="temp")]
    assert mf.output_names(noise, wf) == []


def test_output_names_never_take_a_strange_extension_from_comfyui():
    names = mf.output_names([out_file("2", "evil.png/../../x.EXE?"), out_file("2", "noext")], smoke_wf())
    assert [l for _, l, _ in names] == ["image_1.bin", "image_2.bin"]


def test_environment_from_recorded_system_stats():
    env = mf.environment(recorded("system_stats.json"), COMMIT, False, "595.97")
    assert env["comfyui_version"] == "0.42.1" and env["comfyui_commit"] == COMMIT
    assert env["gpu"] and not env["gpu"].startswith("cuda:") and " : " not in env["gpu"]
    assert env["vram_mb"] > 0 and env["torch"].startswith("2.")
    assert "--disable-metadata" in env["argv"]


def test_server_argv_hides_machine_paths_but_keeps_values():
    argv = ["main.py", "--port", "8188", "--output-directory", "C:/Users/me/out", "--disable-metadata"]
    assert mf.server_argv(argv) == ["--port", "8188", "--output-directory", "<path>", "--disable-metadata"]


@pytest.mark.parametrize("device, name", [
    ("cuda:0 NVIDIA GeForce RTX 3070 Laptop GPU : cudaMallocAsync", "NVIDIA GeForce RTX 3070 Laptop GPU"),
    ("cpu", "cpu"), (None, None), ("", None),
])
def test_gpu_name(device, name):
    assert mf.gpu_name(device) == name


def test_git_head_detached_branch_packed_and_missing(tmp_path):
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text(COMMIT + "\n")
    assert mf.git_head(tmp_path) == COMMIT
    (git / "HEAD").write_text("ref: refs/heads/master\n")
    (git / "packed-refs").write_text("# pack-refs\n%s refs/heads/master\n" % COMMIT)
    assert mf.git_head(tmp_path) == COMMIT
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "refs" / "heads" / "master").write_text("1" * 40 + "\n")
    assert mf.git_head(tmp_path) == "1" * 40  # a loose ref wins over packed-refs
    assert mf.git_head(tmp_path / "nope") is None


def test_iso_utc_keeps_milliseconds():
    assert mf.iso_utc(1792485301954) == "2026-10-20T08:35:01.954Z"
    assert mf.iso_utc(None) is None


jsonschema = pytest.importorskip("jsonschema", reason="pip install -r requirements-maya.txt")
SCHEMA = json.loads((DEFAULT_DIR / "gen_manifest.schema.json").read_text(encoding="utf-8"))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


def sample_manifest(error=None, recording="history_success.json"):
    wf = smoke_wf()
    rec = recorded(recording)
    ex = hist.parse_history(rec["entry"], rec["graph"])
    outputs = [] if error else [{"name": "image", "path": "out/gen/runs/x/image.png", "sha256": SHA, "bytes": 158}]
    return mf.build(wf=wf, workflow_path="gen/workflows/smoke_empty_image.json",
                    params={"size": 64, "color": 1}, rid="smoke_empty_image-0123456789ab",
                    prompt_id="p", models=[(RULES.models["zimg"], SHA)],
                    env=mf.environment(recorded("system_stats.json"), COMMIT, False, "595.97"),
                    execution=ex, outputs=outputs, error=error)


def test_schema_itself_is_valid_and_matches_the_code():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["properties"]["schema_version"]["const"] == mf.SCHEMA_VERSION


def test_ok_and_error_manifests_match_the_schema():
    ok = sample_manifest()
    assert sorted(e.message for e in VALIDATOR.iter_errors(ok)) == []
    assert (ok["status"], ok["exit_code"], list(ok["params"])) == ("ok", 0, ["color", "size"])
    rec = recorded("history_execution_error.json")
    err = hist.parse_history(rec["entry"], rec["graph"]).error
    bad = sample_manifest(error=err, recording="history_execution_error.json")
    assert sorted(e.message for e in VALIDATOR.iter_errors(bad)) == []
    assert (bad["status"], bad["exit_code"]) == ("error", 2)


def test_schema_rejects_a_bad_run_id_and_unknown_fields():
    m = sample_manifest()
    m["run_id"] = "smoke_empty_image-XYZ"
    m["extra"] = 1
    assert len(list(VALIDATOR.iter_errors(m))) == 2
