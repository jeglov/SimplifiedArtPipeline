"""agent.core.safety: which paths a tool may touch. Pure, <1 s."""
from __future__ import annotations

import pytest

from agent.core import safety


@pytest.fixture
def repo(tmp_path):
    for d in ("out/agent", "out_evil", "conventions", "banners/kits/game"):
        (tmp_path / d).mkdir(parents=True)
    return tmp_path


@pytest.mark.parametrize("path", ["out/agent", "out", "out/agent/x.json", "out/agent/../agent/x.json"])
def test_paths_inside_are_allowed(repo, path):
    assert safety.inside(path, "out", root=repo)


@pytest.mark.parametrize("path", [
    "CLAUDE.md",
    "out/../CLAUDE.md",
    "out_evil/x.json",            # a prefix of "out" in characters, not a folder under it
    "../outside.json",
    "C:/Windows/win.ini",
])
def test_paths_outside_are_refused(repo, path):
    with pytest.raises(ValueError, match="refused"):
        safety.inside(path, "out", root=repo)


def test_an_absolute_path_inside_is_allowed(repo):
    assert safety.inside(str(repo / "out" / "agent" / "y.json"), "out", root=repo).name == "y.json"


def test_several_roots(repo):
    assert safety.inside("banners/kits/game/hero.png", "out", "banners/kits", root=repo)
    with pytest.raises(ValueError, match="outside out, banners/kits"):
        safety.inside("conventions/x.yaml", "out", "banners/kits", root=repo)
    with pytest.raises(ValueError, match="no allowed folder"):
        safety.inside("out/x", root=repo)


def test_rel(repo):
    assert safety.rel(repo / "out" / "a.json", root=repo) == "out/a.json"
    assert safety.rel(repo.parent / "elsewhere.json", root=repo).endswith("elsewhere.json")
