"""gen.cli / gen.comfy without ComfyUI: a fake server replays recorded replies.

Pure Python, <1 s. Covers what is hard to provoke on the real server:
reuse, tampering, refusals, run-time errors, cleanup after a crash, model
pins. The real server runs in tests/generation/ (marker "comfy").
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from gen import cli, comfy
from gen.core import ToolError
from gen.core import rules as gr
from gen.core.rules import ComfyRules, ModelPin

FIX = Path(__file__).resolve().parent / "fixtures" / "comfy"
PNG = b"\x89PNG\r\n\x1a\nfake"
COMMIT = "63be2f9b82ad0dac88a7ceaa28a9cf3154125a18"


def recorded(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class FakeServer:
    """Stands in for gen.comfy's process + HTTP functions."""

    def __init__(self, history="history_success.json", refusal=None, crash=None):
        self.history, self.refusal, self.crash = history, refusal, crash
        self.started = self.stopped = self.queued = 0

    def install(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli.comfy, "start", self.start)
        monkeypatch.setattr(cli.comfy, "stop", self.stop)
        monkeypatch.setattr(cli.comfy, "queue", self.queue)
        monkeypatch.setattr(cli.comfy, "wait", self.wait)
        monkeypatch.setattr(cli.comfy, "download", lambda client, q: PNG)
        monkeypatch.setattr(cli.comfy.Client, "stats", lambda self: recorded("system_stats.json"))
        monkeypatch.setattr(cli.comfy, "comfy_dir", lambda env=None: tmp_path)
        monkeypatch.setattr(cli.comfy, "local_changes", lambda d: False)
        monkeypatch.setattr(cli.comfy, "gpu_driver", lambda: "595.97")
        monkeypatch.setattr(cli.mf, "git_head", lambda d: COMMIT)
        return self

    def start(self, rules, env=None):
        self.started += 1
        return True

    def stop(self, *a, **k):
        self.stopped += 1
        return True

    def queue(self, client, graph, client_id):
        self.queued += 1
        self.graph = graph
        if self.refusal:
            r = recorded("prompt_refusals.json")[self.refusal]
            return None, (r["status"], r["body"])
        return "prompt-1", None

    def wait(self, client, prompt_id, timeout_s):
        if self.crash:
            raise self.crash
        entry = recorded(self.history)["entry"]
        # Point the recorded output at the node ids of the graph we sent.
        return entry


def run(tmp_path, *params, **kw):
    return cli.run("smoke_empty_image", list(params), out_dir=tmp_path / "runs", **kw)


def manifest(tmp_path):
    (path,) = (tmp_path / "runs").glob("*/manifest.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_success_writes_files_and_a_manifest(monkeypatch, tmp_path):
    fake = FakeServer().install(monkeypatch, tmp_path)
    code, msg = run(tmp_path)
    assert code == cli.EXIT_OK and msg.startswith("OK ")
    m = manifest(tmp_path)
    assert m["status"] == "ok" and m["environment"]["comfyui_commit"] == COMMIT
    (out,) = m["outputs"]
    assert out["name"] == "image" and out["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert (tmp_path / "runs" / m["run_id"] / "image.png").read_bytes() == PNG
    assert fake.graph["2"]["inputs"]["filename_prefix"] == "artpipe/%s/image" % m["run_id"]
    assert (fake.started, fake.stopped) == (1, 1)


def test_same_request_is_reused_without_starting_comfyui(monkeypatch, tmp_path):
    fake = FakeServer().install(monkeypatch, tmp_path)
    run(tmp_path)
    code, msg = run(tmp_path)
    assert code == cli.EXIT_OK and "reused" in msg
    assert (fake.started, fake.queued) == (1, 1)


def test_a_different_param_is_a_new_run(monkeypatch, tmp_path):
    FakeServer().install(monkeypatch, tmp_path)
    run(tmp_path)
    run(tmp_path, "color=255")
    assert len(list((tmp_path / "runs").iterdir())) == 2


def test_tampered_output_is_regenerated_not_reused(monkeypatch, tmp_path):
    fake = FakeServer().install(monkeypatch, tmp_path)
    run(tmp_path)
    (png,) = (tmp_path / "runs").glob("*/image.png")
    png.write_bytes(b"edited by hand")
    code, msg = run(tmp_path)
    assert code == cli.EXIT_OK and "reused" not in msg and fake.queued == 2
    assert png.read_bytes() == PNG


def test_failed_run_is_never_reused(monkeypatch, tmp_path):
    fake = FakeServer(history="history_execution_error.json").install(monkeypatch, tmp_path)
    assert run(tmp_path)[0] == cli.EXIT_TOOL_ERROR
    assert run(tmp_path)[0] == cli.EXIT_TOOL_ERROR
    assert fake.queued == 2


def test_runtime_error_is_a_tool_error_with_a_manifest(monkeypatch, tmp_path):
    FakeServer(history="history_execution_error.json").install(monkeypatch, tmp_path)
    code, msg = run(tmp_path)
    assert code == cli.EXIT_TOOL_ERROR
    # One line an agent can act on: what failed, where, and why.
    assert "execution_error in node 1 (LoadImage): cannot identify image file" in msg
    m = manifest(tmp_path)
    assert m["status"] == "error" and m["outputs"] == []
    assert m["error"]["exception_type"] == "PIL.UnidentifiedImageError"


def test_refused_graph_is_recorded_too(monkeypatch, tmp_path):
    fake = FakeServer(refusal="value_below_min").install(monkeypatch, tmp_path)
    code, msg = run(tmp_path)
    assert code == cli.EXIT_TOOL_ERROR and "value_smaller_than_min" in msg
    m = manifest(tmp_path)
    assert m["error"]["type"] == "prompt_refused" and m["execution"]["prompt_id"] is None
    assert fake.stopped == 1


def test_success_without_declared_outputs_is_an_error(monkeypatch, tmp_path):
    FakeServer(history="history_cached.json").install(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.mf, "output_names", lambda files, wf: [])
    code, msg = run(tmp_path)
    assert code == cli.EXIT_TOOL_ERROR and "no_outputs" in msg


def test_server_is_stopped_even_when_the_run_crashes(monkeypatch, tmp_path):
    fake = FakeServer(crash=RuntimeError("a bug in the harness")).install(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError):
        run(tmp_path)
    assert fake.stopped == 1


def test_a_server_that_dies_mid_run_is_a_recorded_tool_error(monkeypatch, tmp_path):
    # Probed: ComfyUI died with a Windows access violation in the text encoder;
    # the next /history poll got "connection refused".
    fake = FakeServer(crash=comfy.ServerLost("ComfyUI at http://127.0.0.1:8188 stopped answering")).install(
        monkeypatch, tmp_path)
    log = tmp_path / "server.log"
    log.write_text("[INFO] Requested to load ZImageTEModel_\nWindows fatal exception: access violation\n\n"
                   "Stack (most recent call first):\n  File 'llama.py', line 553 in precompute_freqs_cis\n"
                   + "  File 'x.py', line 1 in f\n" * 30, encoding="utf-8")
    monkeypatch.setattr(cli.comfy, "WORK", tmp_path)
    code, msg = run(tmp_path)
    m = manifest(tmp_path)
    assert code == cli.EXIT_TOOL_ERROR and m["status"] == "error" and m["error"]["type"] == "server_lost"
    assert "access violation" in m["error"]["message"] and "precompute_freqs_cis" in m["error"]["message"]
    assert m["error"]["message"].count("x.py") <= 3  # the crash, not 30 lines of stack
    assert fake.stopped == 1
    assert cli.reusable(next((tmp_path / "runs").iterdir())) is None  # never reused


def test_a_run_that_times_out_is_a_recorded_tool_error(monkeypatch, tmp_path):
    FakeServer(crash=comfy.RunTimeout("the run did not finish within 1800 s")).install(monkeypatch, tmp_path)
    code, _ = run(tmp_path)
    assert code == cli.EXIT_TOOL_ERROR and manifest(tmp_path)["error"]["type"] == "timeout"


def test_connection_refused_is_server_lost_but_stats_is_just_none():
    client = comfy.Client(9)  # discard port: nothing listens
    with pytest.raises(comfy.ServerLost):
        client.get_json("/history/x")
    assert client.stats() is None


def test_keep_server_and_a_server_we_did_not_start_are_left_running(monkeypatch, tmp_path):
    fake = FakeServer().install(monkeypatch, tmp_path)
    run(tmp_path, keep_server=True)
    monkeypatch.setattr(cli.comfy, "start", lambda rules, env=None: False)
    run(tmp_path, "color=1")
    assert fake.stopped == 0


def test_main_maps_errors_to_exit_2(monkeypatch, capsys):
    assert cli.main(["run", "no_such_workflow"]) == cli.EXIT_TOOL_ERROR
    assert "cannot read workflow" in capsys.readouterr().err


def test_param_is_name_value_pairs_on_the_command_line(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(cli, "run", lambda wf, pairs, out, keep: seen.update(pairs=pairs) or (0, "OK"))
    cli.main(["run", "smoke_empty_image", "--param", "color", "255", "--param", "size", "32"])
    assert seen["pairs"] == ["color=255", "size=32"]


# --- model pins ---------------------------------------------------------------------

def make_model(tmp_path, data=b"weights"):
    f = tmp_path / "models" / "diffusion_models" / "z.safetensors"
    f.parent.mkdir(parents=True)
    f.write_bytes(data)
    return f


def model_pin(sha):
    return ModelPin("zimg", "diffusion_models", "z.safetensors", sha, "Apache-2.0", "https://x")


@pytest.fixture
def hash_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "HASH_CACHE", tmp_path / "hashes.json")
    return tmp_path / "hashes.json"


def test_pinned_model_passes(tmp_path, hash_cache):
    make_model(tmp_path)
    sha = hashlib.sha256(b"weights").hexdigest()
    assert cli.verify_models([model_pin(sha)], tmp_path / "models") == [(model_pin(sha), sha)]


def test_swapped_model_file_is_refused(tmp_path, hash_cache):
    make_model(tmp_path, b"other weights")
    with pytest.raises(ToolError, match="different file under the pinned name"):
        cli.verify_models([model_pin(hashlib.sha256(b"weights").hexdigest())], tmp_path / "models")


def test_missing_model_names_its_download_source(tmp_path, hash_cache):
    (tmp_path / "models").mkdir()
    with pytest.raises(ToolError, match="https://x"):
        cli.verify_models([model_pin("ab" * 32)], tmp_path / "models")


def test_hash_cache_skips_rehashing_but_not_a_replaced_file(monkeypatch, tmp_path, hash_cache):
    f = make_model(tmp_path)
    calls = []
    real = cli.sha256_file
    monkeypatch.setattr(cli, "sha256_file", lambda p: calls.append(p) or real(p))
    first = cli.cached_sha256(f)
    assert cli.cached_sha256(f) == first and len(calls) == 1
    f.write_bytes(b"replaced!")  # new size and mtime
    assert cli.cached_sha256(f) == hashlib.sha256(b"replaced!").hexdigest() and len(calls) == 2


# --- gen.comfy pure parts -----------------------------------------------------------

RULES = ComfyRules("0.42.1", COMMIT, 8188, 180, 1800)


def test_server_command_always_has_the_pipeline_flags(tmp_path):
    cmd = comfy.server_command(Path("python.exe"), 8188, tmp_path / "c.yaml", tmp_path)
    for flag in comfy.FLAGS:
        assert flag in cmd
    assert "--disable-metadata" in cmd and "--disable-all-custom-nodes" in cmd
    assert cmd[cmd.index("--listen") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--output-directory") + 1] == str(tmp_path / "output")


def test_is_ours_by_output_directory(tmp_path):
    ours = {"system": {"argv": ["main.py", "--output-directory", str(tmp_path / "output")]}}
    desktop = {"system": {"argv": ["main.py", "--output-directory", "C:/elsewhere"]}}
    assert comfy.is_ours(ours, tmp_path) and not comfy.is_ours(desktop, tmp_path)
    assert not comfy.is_ours({"system": {"argv": ["main.py", "--output-directory"]}}, tmp_path)
    assert not comfy.is_ours({}, tmp_path)


def test_model_paths_config_is_yaml_readable_and_maps_every_folder(tmp_path):
    yaml = pytest.importorskip("yaml")
    for sub in ("vae", "diffusion_models"):
        (tmp_path / sub).mkdir()
    (tmp_path / "readme.txt").write_text("x")
    data = yaml.safe_load(comfy.model_paths_config(tmp_path))["artpipe"]
    assert data["base_path"] == str(tmp_path) and data["is_default"] is True
    assert {k: v for k, v in data.items() if k not in ("base_path", "is_default")} == \
        {"diffusion_models": "diffusion_models/", "vae": "vae/"}


def test_wrong_comfyui_commit_is_refused_before_starting(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("0" * 40)
    with pytest.raises(ToolError, match="pinned to 63be2f9"):
        comfy.check_install(tmp_path, RULES)


def test_edited_comfyui_is_refused(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text(COMMIT)
    monkeypatch.setattr(comfy, "local_changes", lambda d: True)
    with pytest.raises(ToolError, match="local edits"):
        comfy.check_install(tmp_path, RULES)


def test_running_server_of_another_version_is_refused():
    with pytest.raises(ToolError, match="pinned is 0.42.1"):
        comfy.check_running({"system": {"comfyui_version": "0.43.0"}}, RULES)


def test_foreign_server_on_our_port_is_a_tool_error(monkeypatch, tmp_path):
    monkeypatch.setattr(comfy.Client, "stats", lambda self: {"system": {"argv": ["main.py"]}})
    with pytest.raises(ToolError, match="Desktop app"):
        comfy.start(RULES, {}, tmp_path)


def test_stop_without_a_record_touches_nothing(tmp_path):
    assert comfy.stop(tmp_path) is False


def test_comfy_dir_and_models_dir_explain_the_env_vars(tmp_path):
    with pytest.raises(ToolError, match="ARTPIPE_COMFYUI"):
        comfy.comfy_dir({"ARTPIPE_COMFYUI": str(tmp_path)})
    with pytest.raises(ToolError, match="ARTPIPE_COMFY_MODELS"):
        comfy.models_dir({"ARTPIPE_COMFY_MODELS": str(tmp_path / "none")})


def test_error_messages_in_the_manifest_carry_no_machine_paths(monkeypatch, tmp_path):
    # Backlog #46: ComfyUI's messages quote absolute paths; manifests must be shareable.
    where = str(comfy.ROOT / "out" / "gen" / "comfy" / "input" / "ab.png")
    FakeServer(crash=comfy.ServerLost("lost while reading %s and %s" % (where, where.replace("\\", "/")))
               ).install(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.comfy, "WORK", tmp_path)
    run(tmp_path)
    message = manifest(tmp_path)["error"]["message"]
    assert str(comfy.ROOT) not in message and str(comfy.ROOT).replace("\\", "/") not in message
    assert message.count("<repo>") == 2 and str(Path.home()) not in message


def test_machine_swaps_put_the_most_specific_folder_first(tmp_path):
    (tmp_path / "ComfyUI").mkdir()
    (tmp_path / "ComfyUI" / "main.py").write_text("", encoding="utf-8")
    (tmp_path / "models").mkdir()
    swaps = cli.machine_swaps({"ARTPIPE_COMFYUI": str(tmp_path / "ComfyUI"),
                               "ARTPIPE_COMFY_MODELS": str(tmp_path / "models")})
    assert [t for _, t in swaps] == ["<comfyui>", "<models>", "<repo>", "<home>"]
    # ComfyUI not installed: only the folders that exist are swapped
    assert [t for _, t in cli.machine_swaps({"ARTPIPE_COMFYUI": str(tmp_path / "none"),
                                             "ARTPIPE_COMFY_MODELS": str(tmp_path / "none")})] == ["<repo>", "<home>"]
