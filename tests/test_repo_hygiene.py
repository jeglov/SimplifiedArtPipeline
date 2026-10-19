"""Repository rules that once broke the suite."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def test_test_file_names_are_unique():
    # tests/ has no __init__.py files (pytest "prepend" import mode), so two
    # test files with the same basename in different folders can't both be
    # imported: the whole suite fails to collect. Happened once with
    # test_build.py; each file passed when run alone.
    names = Counter(p.name for p in TESTS.rglob("test_*.py"))
    dupes = sorted(n for n, k in names.items() if k > 1)
    assert not dupes, dupes
