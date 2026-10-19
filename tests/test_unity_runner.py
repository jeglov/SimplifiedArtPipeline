"""pipeline.unity without Unity: how Unity's exit code + report become 0/1/2.

Pure Python, runs anywhere in <1 s. The real Editor runs are in
tests/unity_editor/ (marker "unity").
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import unity as u


@pytest.fixture
def log(tmp_path):
    p = tmp_path / "unity.log"
    p.write_text("normal log\n", encoding="utf-8")
    return p


def write_report(tmp_path: Path, status: str) -> Path:
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"status": status}), encoding="utf-8")
    return p


@pytest.mark.parametrize("status, rc, expected", [
    ("pass", 0, u.EXIT_PASS),
    ("fail", 1, u.EXIT_FAIL),
    ("error", 2, u.EXIT_TOOL_ERROR),
])
def test_report_and_exit_code_agree(tmp_path, log, status, rc, expected):
    code, _ = u.reconcile(rc, write_report(tmp_path, status), log)
    assert code == expected


def test_missing_report_is_tool_error_even_when_unity_says_1(tmp_path, log):
    # Unity exits 1 on an unhandled exception or a missing -executeMethod
    # target. Without a report that must NOT read as "asset failed".
    code, msg = u.reconcile(1, tmp_path / "none.json", log)
    assert code == u.EXIT_TOOL_ERROR
    assert "wrote no report" in msg


@pytest.mark.parametrize("status, rc", [("pass", 1), ("fail", 0), ("fail", 2), ("pass", 2)])
def test_disagreement_is_tool_error(tmp_path, log, status, rc):
    code, msg = u.reconcile(rc, write_report(tmp_path, status), log)
    assert code == u.EXIT_TOOL_ERROR
    assert "disagrees" in msg


@pytest.mark.parametrize("content", ["{not json", json.dumps({"no_status": 1}),
                                     json.dumps({"status": "maybe"}), "[]"])
def test_unreadable_report_is_tool_error(tmp_path, log, content):
    p = tmp_path / "r.json"
    p.write_text(content, encoding="utf-8")
    assert u.reconcile(0, p, log)[0] == u.EXIT_TOOL_ERROR


def test_project_lock_gets_a_hint(tmp_path):
    log = tmp_path / "unity.log"
    log.write_text("It looks like another Unity instance is running with this project open.\n",
                   encoding="utf-8")
    _, msg = u.reconcile(1, tmp_path / "none.json", log)
    assert "close it and retry" in msg


def write_results(tmp_path: Path, total: int, passed: int, failed: int) -> Path:
    p = tmp_path / "results.xml"
    p.write_text('<test-run total="%d" passed="%d" failed="%d" skipped="0"/>'
                 % (total, passed, failed), encoding="utf-8")
    return p


def test_tests_pass(tmp_path, log):
    code, msg = u.summarize_tests(0, write_results(tmp_path, 3, 3, 0), log)
    assert code == u.EXIT_PASS
    assert "3 tests: 3 passed, 0 failed" in msg


def test_tests_fail(tmp_path, log):
    # Unity's -runTests exits 2 when a test fails; for us that's 1 ("the
    # thing under test is bad"), not a tool error.
    assert u.summarize_tests(2, write_results(tmp_path, 3, 2, 1), log)[0] == u.EXIT_FAIL


def test_zero_tests_is_never_a_pass(tmp_path, log):
    # Boundary: total = 0. A test assembly that failed to compile runs nothing.
    assert u.summarize_tests(0, write_results(tmp_path, 0, 0, 0), log)[0] == u.EXIT_TOOL_ERROR


@pytest.mark.parametrize("rc, failed", [(0, 1), (2, 0), (1, 0), (3, 1)])
def test_tests_disagreement_or_unknown_rc_is_tool_error(tmp_path, log, rc, failed):
    results = write_results(tmp_path, 2, 2 - failed, failed)
    assert u.summarize_tests(rc, results, log)[0] == u.EXIT_TOOL_ERROR


def test_missing_results_is_tool_error(tmp_path, log):
    assert u.summarize_tests(0, tmp_path / "none.xml", log)[0] == u.EXIT_TOOL_ERROR


def test_editor_version_is_read_from_project(tmp_path):
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 6000.5.8f1\nm_EditorVersionWithRevision: 6000.5.8f1 (abc)\n",
        encoding="utf-8")
    assert u.editor_version(tmp_path) == "6000.5.8f1"


def test_find_unity_uses_the_pinned_version_not_the_newest(tmp_path):
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 6000.5.8f1\n", encoding="utf-8")
    for version in ("6000.5.8f1", "6000.9.0f1"):
        exe = tmp_path / "pf" / "Unity" / "Hub" / "Editor" / version / "Editor" / "Unity.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
    found = u.find_unity({"ProgramFiles": str(tmp_path / "pf")}, tmp_path)
    assert "6000.5.8f1" in found.parts


def test_find_unity_env_override_must_exist(tmp_path):
    with pytest.raises(u.ToolError, match="missing file"):
        u.find_unity({"UNITY": str(tmp_path / "nope.exe")}, tmp_path)


def test_every_run_targets_android():
    cmd = u.base_command(Path("Unity.exe"), Path("x.log"))
    assert cmd[cmd.index("-buildTarget") + 1] == "Android"


def test_open_editor_is_detected(tmp_path):
    (tmp_path / "Temp").mkdir()
    (tmp_path / "Temp" / "UnityLockfile").write_bytes(b"")

    def held(path, mode):
        raise PermissionError(13, "Permission denied", path)

    assert u.project_locked(tmp_path, opener=held) is True


def test_leftover_lockfile_after_a_crash_is_not_a_lock(tmp_path):
    (tmp_path / "Temp").mkdir()
    (tmp_path / "Temp" / "UnityLockfile").write_bytes(b"")
    assert u.project_locked(tmp_path) is False


def test_no_lockfile_is_not_a_lock(tmp_path):
    assert u.project_locked(tmp_path) is False


def test_locked_project_is_a_tool_error_before_unity_starts(monkeypatch, capsys):
    monkeypatch.setattr(u, "find_unity", lambda: Path("Unity.exe"))
    monkeypatch.setattr(u, "project_locked", lambda: True)
    monkeypatch.setattr(u, "run_unity", lambda cmd: pytest.fail("Unity must not be launched"))
    assert u.main(["verify"]) == u.EXIT_TOOL_ERROR
    assert "Close it and retry" in capsys.readouterr().err


@pytest.mark.parametrize("line", [
    "No valid Unity Editor license found. Please activate your license.",
    "[Licensing::Module] Error: License is not active",
    "Failed to activate the license",
])
def test_licence_failure_gets_a_hint(tmp_path, line):
    log = tmp_path / "unity.log"
    log.write_text("startup\n%s\n" % line, encoding="utf-8")
    code, msg = u.reconcile(1, tmp_path / "none.json", log)
    assert code == u.EXIT_TOOL_ERROR  # never "asset failed"
    assert "licence" in msg


def test_normal_licensing_noise_is_not_a_licence_failure(tmp_path):
    # Logged by every successful run on this machine (probed in step 1).
    log = tmp_path / "unity.log"
    log.write_text("[Licensing::Module] Error: Access token is unavailable; failed to update\n", encoding="utf-8")
    assert "licence" not in u.reconcile(1, tmp_path / "none.json", log)[1]


def test_import_passes_absolute_files_and_class(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd):
        seen["cmd"] = cmd
        return 2

    monkeypatch.setattr(u, "find_unity", lambda: Path("Unity.exe"))
    monkeypatch.setattr(u, "project_locked", lambda: False)
    monkeypatch.setattr(u, "run_unity", fake_run)
    u.main(["import", "--class", "prop_small", "rel/SM_A.fbx", "--report", str(tmp_path / "r.json")])
    cmd = seen["cmd"]
    assert cmd[cmd.index("--class") + 1] == "prop_small"
    f = Path(cmd[cmd.index("--file") + 1])
    assert f.is_absolute() and f.name == "SM_A.fbx"
