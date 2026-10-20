"""Path rules for the tools: what an agent may name, resolved and checked in one place. Stdlib, Python 3.9."""
from __future__ import annotations

from pathlib import Path

from agent.core.commands import ROOT


def inside(path: str, *roots: str, root: Path = ROOT) -> Path:
    """`path` (repo-relative or absolute) resolved, refused unless it lies under one of `roots` (repo-relative).

    Resolving first means `out/../CLAUDE.md`, a symlink out of out/, or an
    absolute path elsewhere are all judged by where they really point.
    """
    if not roots:
        raise ValueError("no allowed folder given")
    p = Path(path) if Path(path).is_absolute() else root / path
    p = p.resolve()
    for r in roots:
        base = (root / r).resolve()
        if p == base or base in p.parents:
            return p
    raise ValueError("%s is outside %s: refused" % (path, ", ".join(roots)))


def rel(p: Path, root: Path = ROOT) -> str:
    """Repo-relative POSIX path for a result, or the path itself when it lies outside the repo."""
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(p)
