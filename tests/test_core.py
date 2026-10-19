"""Unit tests for validate.core - no Maya required.

Run under mayapy (proves Python 3.9 compatibility):
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" -m pytest tests/test_core.py -v
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from validate.core.result import (
    EXIT_FAIL,
    EXIT_PASS,
    EXIT_TOOL_ERROR,
    OFFENDER_CAP,
    CheckResult,
    Report,
    Severity,
    Status,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _fail(check_id="topology.ngons", severity=Severity.ERROR, offenders=("a",), **kw):
    return CheckResult(
        id=check_id, passed=False, severity=severity, message="m",
        fixable=False, offenders=offenders, **kw
    )


def _ok(check_id="uv.present"):
    return CheckResult(id=check_id, passed=True, severity="error", message="ok", fixable=False)


# --- CheckResult --------------------------------------------------------------

def test_severity_accepts_plain_string():
    assert _ok().severity is Severity.ERROR


def test_bad_severity_rejected():
    with pytest.raises(ValueError):
        CheckResult(id="a.b", passed=True, severity="err", message="", fixable=False)


@pytest.mark.parametrize("bad_id", ["ngons", "Topology.ngons", "topology.", "a.b.c", "topology-ngons"])
def test_bad_ids_rejected(bad_id):
    with pytest.raises(ValueError):
        _ok(bad_id)


def test_offenders_sorted_deduped_and_capped():
    many = ["|m.f[%d]" % i for i in reversed(range(OFFENDER_CAP + 25))] + ["|m.f[0]"]
    full = _fail(offenders=many)
    assert full.offender_count == OFFENDER_CAP + 25  # total kept, duplicate removed
    assert len(full.offenders) == OFFENDER_CAP + 25  # all kept in memory
    r = full.to_dict()  # ...capped when written
    assert r["offender_count"] == OFFENDER_CAP + 25
    assert len(r["offenders"]) == OFFENDER_CAP
    # Natural order, and the cap keeps the lowest indices.
    assert r["offenders"] == ["|m.f[%d]" % i for i in range(OFFENDER_CAP)]


def test_max_offenders_configurable():
    many = ["|m.f[%d]" % i for i in range(120)]
    assert len(_report([_fail(offenders=many)]).to_dict()["results"][0]["offenders"]) == OFFENDER_CAP
    unlimited = _report([_fail(offenders=many)], max_offenders=0).to_dict()["results"][0]
    assert len(unlimited["offenders"]) == 120 and unlimited["offender_count"] == 120
    assert _report([_fail(offenders=many)], max_offenders=3).to_dict()["results"][0]["offenders"] == [
        "|m.f[0]", "|m.f[1]", "|m.f[2]"]
    with pytest.raises(ValueError):
        _report([_fail()], max_offenders=-1)


def test_offenders_natural_order():
    r = _fail(offenders=["|m.e[10]", "|m.vtx[2]", "|m.e[3]", "|a.e[11]"])
    assert list(r.offenders) == ["|a.e[11]", "|m.e[3]", "|m.e[10]", "|m.vtx[2]"]


def test_passed_with_offenders_is_a_bug():
    with pytest.raises(ValueError):
        CheckResult(id="a.b", passed=True, severity="error", message="", fixable=False, offenders=["x"])


def test_floats_rounded_and_negative_zero_collapsed():
    r = _fail(metrics={"area": 1.23456789, "drift": -0.0000001, "nested": {"v": [0.1 + 0.2]}})
    assert r.metrics == {"area": 1.234568, "drift": 0.0, "nested": {"v": [0.3]}}
    assert "-0.0" not in json.dumps(r.to_dict())


@pytest.mark.parametrize("bad", [{1, 2}, float("nan"), float("inf"), object()])
def test_non_json_metrics_rejected(bad):
    with pytest.raises((TypeError, ValueError)):
        _fail(metrics={"x": bad})


def test_result_is_immutable():
    r = _ok()
    with pytest.raises(Exception):
        r.passed = False  # type: ignore[misc]


# --- Report -------------------------------------------------------------------

def _report(results, tool_error=None, **kw):
    return Report(source_path="crate.fbx", source_sha256="ab" * 32,
                  asset_class="prop_small", results=results, tool_error=tool_error, **kw)


def test_status_pass_when_only_warnings_fail():
    rep = _report([_ok(), _fail(severity=Severity.WARNING)])
    assert rep.status is Status.PASS and rep.exit_code == EXIT_PASS
    assert rep.summary()["warnings"] == 1


def test_status_fail_on_error():
    rep = _report([_ok(), _fail()])
    assert rep.status is Status.FAIL and rep.exit_code == EXIT_FAIL


def test_tool_error_overrides_everything():
    rep = _report([_ok()], tool_error="FBX import failed")
    assert rep.status is Status.ERROR and rep.exit_code == EXIT_TOOL_ERROR


def test_duplicate_ids_rejected():
    with pytest.raises(ValueError):
        _report([_ok(), _ok()])


def test_json_is_byte_identical_regardless_of_input_order():
    """Core determinism promise: shuffled offenders/metrics -> same bytes."""
    a = _report([_fail(offenders=["b", "a", "c"], metrics={"y": 1, "x": 2.0000001})])
    b = _report([_fail(offenders=["c", "a", "b", "a"], metrics={"x": 2.0, "y": 1})])
    assert a.to_json() == b.to_json()
    assert a.to_json().endswith("\n")
    json.loads(a.to_json())  # valid JSON


def test_report_has_no_timestamp_fields():
    def keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from keys(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from keys(v)

    all_keys = set(keys(_report([_fail()]).to_dict()))
    assert not any("time" in k or "date" in k for k in all_keys), all_keys


# --- Architecture guard ---------------------------------------------------------

def test_core_never_imports_maya():
    """validate.core must stay importable without Maya (fresh interpreter,
    so modules imported by pytest itself can't mask a violation)."""
    code = (
        "import sys, validate.core.result, validate.core.naming, validate.core.conventions; "
        "bad = [m for m in sys.modules if m == 'maya' or m.startswith('maya.')]; "
        "assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", code], cwd=str(REPO_ROOT), check=True)
