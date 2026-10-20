"""artpipe gen on the real, pinned ComfyUI with the model-free smoke workflow.

What only the real server can prove: the pipeline's flags take effect (no
metadata in the PNG), params reach the graph, ComfyUI's own validation is
reported, and the same graph gives the same bytes after a restart.
"""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest

from gen import cli, comfy
from validate.core.conventions import DEFAULT_DIR

jsonschema = pytest.importorskip("jsonschema", reason="pip install -r requirements-maya.txt")
VALIDATOR = jsonschema.Draft202012Validator(
    json.loads((DEFAULT_DIR / "gen_manifest.schema.json").read_text(encoding="utf-8")))


def png_chunks(data: bytes):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, []
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        chunks.append((data[pos + 4:pos + 8].decode("latin1"), data[pos + 8:pos + 8 + n]))
        pos += 12 + n
    return chunks


def generate(out: Path, *params):
    code, msg = cli.run("smoke_empty_image", list(params), out_dir=out, keep_server=True)
    (path,) = out.glob("*/manifest.json")
    return code, msg, json.loads(path.read_text(encoding="utf-8"))


def test_smoke_run_writes_a_valid_manifest_and_a_clean_png(comfy_server, tmp_path):
    code, msg, m = generate(tmp_path, "size=32")
    assert code == 0, msg
    assert sorted(e.message for e in VALIDATOR.iter_errors(m)) == []
    assert m["environment"]["comfyui_commit"] == comfy_server.commit
    assert m["execution"]["executed_nodes"] == ["1", "2"]  # size=32 is new to this server
    png = (tmp_path / m["run_id"] / "image.png").read_bytes()
    assert hashlib.sha256(png).hexdigest() == m["outputs"][0]["sha256"]
    chunks = png_chunks(png)
    width, height = struct.unpack(">II", chunks[0][1][:8])
    assert (width, height) == (32, 64)  # the size param reached EmptyImage.width
    # --disable-metadata: ComfyUI would otherwise embed the whole graph here.
    assert [c for c, _ in chunks if c in ("tEXt", "iTXt", "zTXt")] == []


def test_comfyui_refusal_is_exit_2_with_a_manifest(comfy_server, tmp_path):
    code, msg, m = generate(tmp_path, "size=0")  # EmptyImage.width min is 1
    assert code == cli.EXIT_TOOL_ERROR and "value_smaller_than_min" in msg
    assert m["error"]["type"] == "prompt_refused" and m["outputs"] == []
    assert sorted(e.message for e in VALIDATOR.iter_errors(m)) == []


def test_same_request_gives_identical_bytes_after_a_restart(comfy_server, tmp_path):
    # Fresh server = no in-memory cache: the graph really runs twice.
    first = generate(tmp_path / "a", "color=4660")[2]
    comfy.stop()
    assert comfy.start(comfy_server) is True
    second = generate(tmp_path / "b", "color=4660")[2]
    assert first["run_id"] == second["run_id"]
    assert second["execution"]["cached_nodes"] == []
    assert first["outputs"][0]["sha256"] == second["outputs"][0]["sha256"]
