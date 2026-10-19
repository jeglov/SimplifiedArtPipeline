"""Step 1: the Unity harness end to end, through the same entry point an
agent uses (pipeline.unity main = `artpipe unity ...`)."""
from __future__ import annotations

import json

from pipeline import unity as u


def canonical(data) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def test_import_stub_passes_and_writes_a_canonical_report(tmp_path):
    report = tmp_path / "a" / "import.report.json"
    assert u.main(["import", "--report", str(report)]) == u.EXIT_PASS
    raw = report.read_bytes()
    data = json.loads(raw)
    assert data["status"] == "pass"
    assert {r["id"] for r in data["preflight"]} == {
        "env.build_target", "env.color_space", "env.render_pipeline", "env.serialization"}
    # Written by C#, byte-identical to what Python's json.dumps would write.
    assert raw == canonical(data)


def test_import_report_is_deterministic(tmp_path):
    first, second = tmp_path / "1.json", tmp_path / "2.json"
    assert u.main(["import", "--report", str(first)]) == u.EXIT_PASS
    assert u.main(["import", "--report", str(second)]) == u.EXIT_PASS
    assert first.read_bytes() == second.read_bytes()


def test_editmode_suite_passes(tmp_path):
    results = tmp_path / "results.xml"
    assert u.main(["test", "--results", str(results)]) == u.EXIT_PASS
