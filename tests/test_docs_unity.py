"""docs/unity_import.md documents every check the Unity package can report, and no more.

Pure Python: reads the C# sources as text. The check table is hand-written
on purpose (each row needs a human "why"), so this test is what keeps it honest.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "unity" / "Packages" / "com.artpipe.importer" / "Editor"
DOC = ROOT / "docs" / "unity_import.md"
ID = r"(?:env|gate|unity|texture)\.[a-z_]+"


def code_ids():
    return {m for f in EDITOR.glob("*.cs") for m in re.findall('"(%s)"' % ID, f.read_text(encoding="utf-8"))}


def doc_ids():
    return set(re.findall(r"^\| `(%s)` \|" % ID, DOC.read_text(encoding="utf-8"), re.M))


def test_every_check_in_the_code_is_documented():
    missing = sorted(code_ids() - doc_ids())
    assert not missing, "add these to the Checks table in docs/unity_import.md: %s" % missing


def test_the_doc_lists_no_check_the_code_lacks():
    stale = sorted(doc_ids() - code_ids())
    assert not stale, "docs/unity_import.md lists checks that no longer exist: %s" % stale


def test_the_scan_finds_something():
    # Guard against a regex that silently matches nothing (both sets empty = "pass").
    assert len(code_ids()) >= 20
