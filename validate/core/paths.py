"""Machine-independent paths for reports.

A report must be identical on every machine for the same asset (diffable,
cacheable by hash). An absolute path like C:\\Users\\Jeglow\\... breaks that
and leaks the machine's layout. So files inside the repo are reported
relative to the repo root, always with forward slashes.

Pure Python (no maya import).
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

REPO_ROOT = Path(__file__).resolve().parents[2]


def report_path(path: Union[str, Path], root: Path = REPO_ROOT) -> str:
    """"C:/.../ArtPipeline/out/x.ma" -> "out/x.ma". Outside the repo: absolute, POSIX style."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:  # not under root
        return resolved.as_posix()
