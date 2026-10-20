"""banners.tools: download, pin and verify the renderer's tools. Pure Python on fake zips, <1 s."""
from __future__ import annotations

import hashlib
import io
import zipfile

import pytest

from banners import tools
from banners.core import ToolError
from banners.core.rules import ToolPin

EXE = b"MZ fake ffmpeg"


def make_zip(path, members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    path.write_bytes(buf.getvalue())
    return hashlib.sha256(buf.getvalue()).hexdigest()


def pin_for(archive_sha, exe=EXE):
    return ToolPin("ffmpeg", "9.0.2", "pkg/bin", "https://example.invalid/ffmpeg.zip", archive_sha,
                   "GPL-3.0-or-later", {"ffmpeg": hashlib.sha256(exe).hexdigest()})


@pytest.fixture
def env(tmp_path):
    return {"ARTPIPE_TOOLS": str(tmp_path / "tools")}


@pytest.fixture
def served(tmp_path, monkeypatch):
    """_download copies a local zip instead of touching the network; counts calls."""
    calls = []

    def fake(url, dest):
        calls.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((tmp_path / "served.zip").read_bytes())
    monkeypatch.setattr(tools, "_download", fake)
    return calls


def test_install_unpacks_only_the_pinned_folder_then_verifies(tmp_path, env, served):
    sha = make_zip(tmp_path / "served.zip", {"pkg/bin/ffmpeg.exe": EXE, "pkg/doc/readme.txt": b"x"})
    pin = pin_for(sha)
    assert "installed 1 files" in tools.install(pin, env)
    assert tools.verify(pin, env) == {"ffmpeg": str(tools.exe_path(pin, "ffmpeg", env))}
    assert not (tools.install_dir(pin, env) / "readme.txt").exists()
    assert "already installed" in tools.install(pin, env) and len(served) == 1


def test_a_download_with_another_hash_is_refused(tmp_path, env, served):
    make_zip(tmp_path / "served.zip", {"pkg/bin/ffmpeg.exe": EXE})
    with pytest.raises(ToolError, match="refusing it"):
        tools.install(pin_for("0" * 64), env)
    assert not tools.install_dir(pin_for("0" * 64), env).exists()


def test_a_swapped_exe_fails_verify_even_with_a_warm_cache(tmp_path, env, served):
    sha = make_zip(tmp_path / "served.zip", {"pkg/bin/ffmpeg.exe": EXE})
    pin = pin_for(sha)
    tools.install(pin, env)
    tools.verify(pin, env)  # warms the size+mtime cache
    exe = tools.exe_path(pin, "ffmpeg", env)
    exe.write_bytes(b"MZ evil ffmpeg")  # same length: only the mtime betrays it
    with pytest.raises(ToolError, match="not the pinned ffmpeg build"):
        tools.verify(pin, env)


def test_a_missing_tool_says_how_to_install(env):
    with pytest.raises(ToolError, match="artpipe banners setup"):
        tools.verify(pin_for("0" * 64), env)


def test_unpack_refuses_zip_slip(tmp_path):
    make_zip(tmp_path / "evil.zip", {"pkg/bin/../../../escape.exe": EXE, "pkg/bin/ok.exe": EXE})
    with pytest.raises(ToolError, match="escapes"):
        tools.unpack(tmp_path / "evil.zip", "pkg/bin", tmp_path / "dest")
    assert not (tmp_path / "escape.exe").exists()


def test_unpack_without_the_folder_is_an_error(tmp_path):
    make_zip(tmp_path / "other.zip", {"elsewhere/ffmpeg.exe": EXE})
    with pytest.raises(ToolError, match="no folder"):
        tools.unpack(tmp_path / "other.zip", "pkg/bin", tmp_path / "dest")


def test_status_never_raises(env):
    from banners.core import rules as br
    state = tools.status(br.load(), env)
    assert state["ffmpeg"]["ok"] is False and "artpipe banners setup" in state["ffmpeg"]["error"]
