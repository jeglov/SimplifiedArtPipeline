"""validate.core.image (PNG/TGA without Pillow) and validate.core.texture (#40).

Pure Python, <1 s. Parity with Unity's own verdicts is proven on the real
Editor in tests/unity_editor/test_texture_parity.py.
"""
from __future__ import annotations

import json
import struct
import zlib

import pytest

from validate import texture_cli
from validate.core import conventions as conv
from validate.core.image import ImageError, decode_png, encode_png, read_info, sample_grid
from validate.core.texture import map_type, source_checks

C = conv.load()


def chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def png_bytes(w, h, ctype=2, depth=8, extra=b"", idat=None, interlace=0):
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = idat if idat is not None else zlib.compress((b"\x00" + b"\x40" * (w * ch * depth // 8)) * h)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, ctype, 0, 0, interlace))
            + extra + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def tga_bytes(w, h, depth=24, alpha_bits=0, image_type=2):
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, image_type, 0, 0, 0, 0, 0, w, h, depth, alpha_bits)
    return header + b"\x00" * (w * h * depth // 8)


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return p


# --- headers ------------------------------------------------------------------------

@pytest.mark.parametrize("ctype, extra, alpha", [
    (2, b"", False), (6, b"", True), (4, b"", True), (0, b"", False),
    (3, chunk(b"PLTE", b"\x00" * 3) + chunk(b"tRNS", b"\x00"), True),   # palette with transparency
    (2, chunk(b"tRNS", b"\x00\x00\x00\x00\x00\x00"), True),             # RGB colour key
])
def test_png_alpha_follows_what_an_engine_sees(tmp_path, ctype, extra, alpha):
    info = read_info(write(tmp_path, "t.png", png_bytes(4, 2, ctype, extra=extra)))
    assert (info.format, info.width, info.height, info.has_alpha) == ("png", 4, 2, alpha)


@pytest.mark.parametrize("depth, bits, alpha", [(24, 0, False), (32, 8, True), (32, 0, False), (16, 1, True)])
def test_tga_alpha_comes_from_the_attribute_bits(tmp_path, depth, bits, alpha):
    info = read_info(write(tmp_path, "t.tga", tga_bytes(8, 4, depth, bits)))
    assert (info.format, info.width, info.height, info.has_alpha) == ("tga", 8, 4, alpha)


@pytest.mark.parametrize("name, data, match", [
    ("bad.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 4, "truncated"),
    ("bad.png", png_bytes(2, 2)[:-12], "no IEND"),
    ("bad.png", png_bytes(2, 2).replace(b"IEND\xaeB`\x82", b"IEND\x00\x00\x00\x00"), "bad CRC"),
    ("bad.jpg", b"\xff\xd8\xff\xe0", "neither"),
    ("bad.tga", b"\x00" * 10, "18-byte"),
    ("bad.tga", tga_bytes(0, 4), "size"),
])
def test_unreadable_files_raise_image_error(tmp_path, name, data, match):
    with pytest.raises(ImageError, match=match):
        read_info(write(tmp_path, name, data))


# --- pixels -------------------------------------------------------------------------

def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


def filtered(rows, bpp, ftype):
    """Reference PNG encoder for one filter type (the spec's forward filters)."""
    out, prev = b"", bytes(len(rows[0]))
    for row in rows:
        enc = bytearray()
        for i, x in enumerate(row):
            a = row[i - bpp] if i >= bpp else 0
            b, c = prev[i], prev[i - bpp] if i >= bpp else 0
            pred = [0, a, b, (a + b) >> 1, _paeth(a, b, c)][ftype]
            enc.append((x - pred) & 0xFF)
        out += bytes([ftype]) + bytes(enc)
        prev = row
    return out


@pytest.mark.parametrize("ftype", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("ctype, bpp", [(2, 3), (6, 4), (0, 1)])
def test_decode_reverses_every_png_filter(tmp_path, ftype, ctype, bpp):
    w, h = 5, 4
    rows = [bytes((x * 37 + y * 91 + k * 13) & 0xFF for x in range(w) for k in range(bpp)) for y in range(h)]
    data = png_bytes(w, h, ctype, idat=zlib.compress(filtered(rows, bpp, ftype)))
    px = decode_png(write(tmp_path, "f.png", data))
    assert (px.width, px.height, px.channels) == (w, h, bpp)
    assert px.data == b"".join(rows)


def test_encode_decode_round_trip_and_accessors(tmp_path):
    data = bytes([10, 20, 30, 255, 40, 50, 60, 0])
    px = decode_png(write(tmp_path, "rt.png", encode_png(2, 1, 4, data)))
    assert px.rgb(1, 0) == (40, 50, 60) and px.alpha(1, 0) == 0 and px.alpha(0, 0) == 255
    grey = decode_png(write(tmp_path, "g.png", encode_png(1, 1, 1, b"\x7f")))
    assert grey.rgb(0, 0) == (127, 127, 127) and grey.alpha(0, 0) == 255


@pytest.mark.parametrize("kw, match", [({"depth": 16}, "8-bit"), ({"ctype": 3}, "8-bit"), ({"interlace": 1}, "8-bit")])
def test_decode_refuses_what_it_cannot_read(tmp_path, kw, match):
    ctype = kw.pop("ctype", 2)
    with pytest.raises(ImageError, match=match):
        decode_png(write(tmp_path, "x.png", png_bytes(2, 2, ctype, **kw)))


def test_decode_reports_corrupt_data(tmp_path):
    with pytest.raises(ImageError, match="corrupt PNG image data"):
        decode_png(write(tmp_path, "x.png", png_bytes(2, 2, idat=b"not zlib")))
    short = zlib.compress(b"\x00\x40\x40\x40")
    with pytest.raises(ImageError, match="expected"):
        decode_png(write(tmp_path, "y.png", png_bytes(2, 2, idat=short)))
    bad_filter = zlib.compress((b"\x07" + b"\x40" * 6) * 2)
    with pytest.raises(ImageError, match="filter type 7"):
        decode_png(write(tmp_path, "z.png", png_bytes(2, 2, idat=bad_filter)))


def test_sample_grid_is_nearest_neighbour():
    from validate.core.image import Pixels
    px = Pixels(4, 4, 1, bytes(range(16)))
    assert [[c[0] for c in row] for row in sample_grid(px, 2)] == [[0, 2], [8, 10]]


# --- texture rules ------------------------------------------------------------------

def tex(tmp_path, name, size=128, alpha=False, w=None):
    w = w or size
    ch = 4 if alpha else 3
    return write(tmp_path, name, encode_png(w, size, ch, b"\x40" * (w * size * ch)))


def verdicts(path, cls="prop_small"):
    return {r.id: r.passed for r in source_checks(path, C, cls)}


def test_good_texture_passes_every_check(tmp_path):
    v = verdicts(tex(tmp_path, "T_Crate_D.png", 128, alpha=True))
    assert v == {"texture.name": True, "texture.source_format": True, "texture.power_of_two": True,
                 "texture.min_size": True, "texture.square": True, "texture.source_size": True,
                 "texture.alpha": True}


def test_map_type_from_suffix():
    assert [map_type(n, C) for n in ("T_Crate_A_N.png", "T_Crate_MS.tga", "T_Crate_X.png", "crate.png")] == \
        ["N", "MS", None, None]


@pytest.mark.parametrize("name", ["T_Crate.png", "T_Crate_Diffuse.png", "Crate_D.png", "T_crate_D.png"])
def test_bad_names_fail_with_an_example(tmp_path, name):
    r = next(r for r in source_checks(tex(tmp_path, name), C, "prop_small") if r.id == "texture.name")
    assert not r.passed and "T_Crate_D.png" in r.message


def test_wrong_format_stops_before_reading_pixels(tmp_path):
    p = write(tmp_path, "T_Crate_D.jpg", b"\xff\xd8\xff")
    assert [(r.id, r.passed) for r in source_checks(p, C, "prop_small")] == \
        [("texture.name", False), ("texture.source_format", False)]


def test_npot_fails(tmp_path):
    assert verdicts(tex(tmp_path, "T_Odd_D.png", 64, w=96))["texture.power_of_two"] is False


def test_min_size_boundary(tmp_path):
    m = C.textures.min_size
    assert verdicts(tex(tmp_path, "T_Min_D.png", m))["texture.min_size"] is True
    assert verdicts(tex(tmp_path, "T_Tiny_D.png", m // 2))["texture.min_size"] is False


def test_source_size_boundary_is_the_class_max_and_only_a_warning(tmp_path):
    limit = C.asset_class("prop_small").texture_max_size
    assert verdicts(tex(tmp_path, "T_Fit_D.png", limit))["texture.source_size"] is True
    big = source_checks(tex(tmp_path, "T_Big_D.png", limit * 2), C, "prop_small")
    r = next(r for r in big if r.id == "texture.source_size")
    assert not r.passed and r.severity.value == "warning" and r.metrics["max_size"] == limit


def test_pickup_is_stricter_than_prop_small(tmp_path):
    p = tex(tmp_path, "T_Coin_D.png", 256)
    assert verdicts(p, "prop_small")["texture.source_size"] is True
    assert verdicts(p, "pickup")["texture.source_size"] is False


def test_without_a_class_the_default_max_applies(tmp_path):
    r = next(r for r in source_checks(tex(tmp_path, "T_Atlas_D.png", 512), C, None) if r.id == "texture.source_size")
    assert r.passed and r.metrics["max_size"] == C.textures.default_max_size


@pytest.mark.parametrize("name, alpha, ok", [
    ("T_A_D.png", True, True), ("T_A_D.png", False, True),     # optional
    ("T_A_N.png", True, False), ("T_A_N.png", False, True),    # forbidden
    ("T_A_MS.png", True, True), ("T_A_MS.png", False, False),  # required
])
def test_alpha_rules_per_map_type(tmp_path, name, alpha, ok):
    assert verdicts(tex(tmp_path, name, 64, alpha=alpha))["texture.alpha"] is ok


def test_failing_checks_name_the_file_as_offender(tmp_path):
    r = next(r for r in source_checks(tex(tmp_path, "T_A_N.png", 64, alpha=True), C, "prop_small",
                                      shown_as="out/T_A_N.png") if r.id == "texture.alpha")
    assert r.offenders == ("out/T_A_N.png",)


# --- CLI ----------------------------------------------------------------------------

jsonschema = pytest.importorskip("jsonschema", reason="pip install -r requirements-maya.txt")
SCHEMA = jsonschema.Draft202012Validator(json.loads((conv.DEFAULT_DIR / "report.schema.json").read_text(encoding="utf-8")))


def test_cli_writes_schema_valid_reports_and_the_worst_exit_code(tmp_path):
    good = tex(tmp_path, "T_Good_D.png", 64)
    bad = tex(tmp_path, "T_Bad_N.png", 64, alpha=True)
    out = tmp_path / "reports"
    assert texture_cli.main([str(good), "--out", str(out), "--class", "prop_small"]) == 0
    assert texture_cli.main([str(good), str(bad), "--out", str(out), "--class", "prop_small"]) == 1
    for name in ("T_Good_D.png", "T_Bad_N.png"):
        data = json.loads((out / (name + ".report.json")).read_text(encoding="utf-8"))
        assert sorted(e.message for e in SCHEMA.iter_errors(data)) == []
        assert data["tool"] == "artpipe.validate.texture"


def test_cli_unreadable_or_missing_file_is_a_tool_error(tmp_path):
    corrupt = write(tmp_path, "T_X_D.png", b"\x89PNG\r\n\x1a\n garbage")
    out = tmp_path / "r"
    assert texture_cli.main([str(corrupt), "--out", str(out)]) == 2
    assert json.loads((out / "T_X_D.png.report.json").read_text())["status"] == "error"
    assert texture_cli.main([str(tmp_path / "T_None_D.png"), "--out", str(out)]) == 2


def test_cli_unknown_class_lists_the_options(tmp_path, capsys):
    assert texture_cli.main([str(tex(tmp_path, "T_A_D.png", 64)), "--class", "prop_huge"]) == 2
    assert "prop_small" in capsys.readouterr().err
