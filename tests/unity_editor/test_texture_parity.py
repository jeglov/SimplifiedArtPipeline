"""Backlog #40: Python's texture checks give Unity's verdict, check for check.

validate.core.texture re-implements TextureRulesUnity.SourceChecks so
generated textures are judged before they reach Unity. This imports the same
files into the real Unity project (one boot, marker "unity") and compares
every check id both sides report.
"""
from __future__ import annotations

import json
import struct

import pytest

from unity_helpers import png
from pipeline import unity as u
from validate.core import conventions as conv
from validate.core.image import encode_png
from validate.core.texture import source_checks


def tga(path, size, alpha):
    depth, descriptor = (32, 8) if alpha else (24, 0)
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, size, size, depth, descriptor)
    path.write_bytes(header + b"\x80" * (size * size * depth // 8))
    return path


@pytest.fixture(scope="module")
def textures(tmp_path_factory, project_root_cleanup):
    d = tmp_path_factory.mktemp("tex")
    files = [
        png(d / "T_ParGood_D.png", 128, alpha=True),
        png(d / "T_ParBig_D.png", 512, alpha=False),        # over prop_small's 256: warning
        png(d / "T_ParTiny_D.png", 16, alpha=False),        # below min_size
        png(d / "T_ParNormal_N.png", 64, alpha=True),       # alpha forbidden
        png(d / "T_ParMetal_MS.png", 64, alpha=False),      # alpha required
        tga(d / "T_ParOcclusion_AO.tga", 64, alpha=False),
        tga(d / "T_ParEmissive_E.tga", 64, alpha=True),     # alpha forbidden, TGA attribute bits
    ]
    odd = d / "T_ParOdd_D.png"                              # 96x64: not a power of two
    odd.write_bytes(encode_png(96, 64, 3, b"\x80" * (96 * 64 * 3)))
    files.append(odd)
    return files


@pytest.fixture(scope="module")
def unity_verdicts(textures, tmp_path_factory):
    report = tmp_path_factory.mktemp("r") / "import.json"
    u.main(["import", "--class", "prop_small"] + [str(f) for f in textures] + ["--report", str(report)])
    data = json.loads(report.read_text(encoding="utf-8"))
    return {a["path"].rsplit("/", 1)[-1]: {r["id"]: (r["passed"], r["severity"]) for r in a["results"]}
            for a in data["assets"]}


def test_every_texture_gets_the_same_verdict_in_python_and_unity(textures, unity_verdicts):
    c = conv.load()
    diffs = []
    for f in textures:
        unity_side = unity_verdicts[f.name]
        python_side = {r.id: (r.passed, r.severity.value) for r in source_checks(f, c, "prop_small")}
        missing = sorted(set(python_side) - set(unity_side))
        assert not missing, (f.name, "Unity doesn't report", missing)
        for cid, verdict in sorted(python_side.items()):
            if unity_side[cid] != verdict:
                diffs.append((f.name, cid, "python", verdict, "unity", unity_side[cid]))
    assert diffs == []


def test_the_fixture_set_exercises_every_failure(textures, unity_verdicts):
    # Guards the parity test itself: if nothing fails, agreement proves nothing.
    failing = {cid for v in unity_verdicts.values() for cid, (ok, _) in v.items() if not ok}
    assert {"texture.power_of_two", "texture.min_size", "texture.source_size", "texture.alpha"} <= failing
