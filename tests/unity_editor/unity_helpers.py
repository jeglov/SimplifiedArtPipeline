"""Helpers shared by the Unity end-to-end tests."""
from __future__ import annotations

import shutil
import struct
import zlib
from pathlib import Path

import pytest

from pipeline import unity as u
from validate.core import conventions as conv


ROOT = u.PROJECT / conv.load().unity_import.root  # unity/Assets/Art


def png(path: Path, size: int, alpha: bool) -> Path:
    """A minimal valid PNG (no Pillow in mayapy): grey, `size` x `size`."""
    channels, color_type = (4, 6) if alpha else (3, 2)
    row = b"\x00" + b"\x80" * (size * channels)
    raw = zlib.compress(row * size)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, color_type, 0, 0, 0))
                     + chunk(b"IDAT", raw) + chunk(b"IEND", b""))
    return path


@pytest.fixture(scope="module")
def project_root_cleanup():
    existed = ROOT.exists()
    before = {p for p in ROOT.rglob("*")} if existed else set()
    yield
    if not existed:
        shutil.rmtree(str(ROOT), ignore_errors=True)
        meta = ROOT.with_name(ROOT.name + ".meta")
        if meta.exists():
            meta.unlink()
    else:
        for p in sorted(set(ROOT.rglob("*")) - before, reverse=True):
            shutil.rmtree(str(p), ignore_errors=True) if p.is_dir() else p.unlink()
