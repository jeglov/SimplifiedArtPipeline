"""Read PNG and TGA files without Pillow (mayapy ships without it).

Two levels:
  * read_info(): the header facts the texture rules need (size, alpha, bit
    depth). Reads a few hundred bytes for TGA and only the chunk headers for
    PNG, so it is cheap on any file.
  * decode_png(): 8-bit pixels of a non-interlaced PNG, for checks that look
    at the image itself (concept images). zlib + the five PNG row filters, in
    pure Python: about 1-2 s for 1024x1024 RGB on mayapy.

"Has alpha" follows what a game engine sees: an alpha channel (PNG colour
types 4/6, TGA attribute bits) or a PNG tRNS chunk (transparency on a
palette or a key colour). Pure Python 3.9.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# PNG colour type -> channels. 3 = palette (1 index per pixel).
_PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


class ImageError(ValueError):
    """Not a readable PNG/TGA, or a variant this reader doesn't decode."""


@dataclass(frozen=True)
class ImageInfo:
    format: str        # "png" | "tga"
    width: int
    height: int
    has_alpha: bool
    bit_depth: int     # bits per channel (PNG) or per pixel (TGA)
    color_type: Optional[int] = None  # PNG only
    interlaced: bool = False


def _png_chunks(data: bytes):
    """(type, payload) for each chunk; checks the signature and every CRC."""
    if data[:8] != PNG_SIGNATURE:
        raise ImageError("not a PNG (bad signature)")
    pos = 8
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        end = pos + 12 + length
        if end > len(data):
            raise ImageError("truncated PNG chunk %r" % kind)
        payload = data[pos + 8:pos + 8 + length]
        crc = struct.unpack(">I", data[pos + 8 + length:end])[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != crc:
            raise ImageError("corrupt PNG: bad CRC in chunk %r" % kind)
        yield kind.decode("latin1"), payload
        if kind == b"IEND":
            return
        pos = end
    raise ImageError("truncated PNG: no IEND chunk")


def _png_info(data: bytes) -> ImageInfo:
    chunks = list(_png_chunks(data))
    if not chunks or chunks[0][0] != "IHDR" or len(chunks[0][1]) != 13:
        raise ImageError("PNG must start with a 13-byte IHDR chunk")
    w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
    if ctype not in _PNG_CHANNELS or w == 0 or h == 0:
        raise ImageError("invalid PNG header (colour type %d, %dx%d)" % (ctype, w, h))
    alpha = ctype in (4, 6) or any(k == "tRNS" for k, _ in chunks)
    return ImageInfo("png", w, h, alpha, depth, ctype, interlace == 1)


def _tga_info(data: bytes) -> ImageInfo:
    if len(data) < 18:
        raise ImageError("not a TGA (shorter than its 18-byte header)")
    image_type = data[2]
    if image_type not in (1, 2, 3, 9, 10, 11):
        raise ImageError("not a TGA (image type %d)" % image_type)
    w, h = struct.unpack("<HH", data[12:16])
    depth, descriptor = data[16], data[17]
    if w == 0 or h == 0:
        raise ImageError("invalid TGA size %dx%d" % (w, h))
    return ImageInfo("tga", w, h, (descriptor & 0x0F) > 0, depth)


def read_info(path: Union[str, Path]) -> ImageInfo:
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise ImageError("cannot read %s: %s" % (p.name, exc))
    if data[:8] == PNG_SIGNATURE:
        return _png_info(data)
    if p.suffix.lower() == ".tga":
        return _tga_info(data)
    raise ImageError("%s is neither a PNG nor a TGA" % p.name)


@dataclass(frozen=True)
class Pixels:
    width: int
    height: int
    channels: int        # 1 grey, 2 grey+alpha, 3 RGB, 4 RGBA
    data: bytes          # row-major, `channels` bytes per pixel

    def rgb(self, x: int, y: int):
        i = (y * self.width + x) * self.channels
        if self.channels >= 3:
            return self.data[i], self.data[i + 1], self.data[i + 2]
        return self.data[i], self.data[i], self.data[i]

    def alpha(self, x: int, y: int) -> int:
        if self.channels in (2, 4):
            return self.data[(y * self.width + x) * self.channels + self.channels - 1]
        return 255


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(raw: bytes, width: int, height: int, bpp: int) -> bytes:
    stride = width * bpp
    if len(raw) != height * (stride + 1):
        raise ImageError("PNG data is %d bytes, expected %d" % (len(raw), height * (stride + 1)))
    out = bytearray(height * stride)
    prev = bytearray(stride)
    for y in range(height):
        ftype = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if ftype == 1:      # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:    # Up
            line = bytearray((a + b) & 0xFF for a, b in zip(line, prev))
        elif ftype == 3:    # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:    # Paeth
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                upleft = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prev[i], upleft)) & 0xFF
        elif ftype != 0:
            raise ImageError("unknown PNG filter type %d in row %d" % (ftype, y))
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return bytes(out)


def decode_png(path: Union[str, Path]) -> Pixels:
    """8-bit, non-interlaced, non-palette PNG -> Pixels. Anything else: ImageError."""
    data = Path(path).read_bytes()
    info = _png_info(data)
    if info.bit_depth != 8 or info.color_type == 3 or info.interlaced:
        raise ImageError("decode_png reads 8-bit non-interlaced grey/RGB(A) only "
                         "(got depth %d, colour type %d, interlaced %s)"
                         % (info.bit_depth, info.color_type, info.interlaced))
    idat = b"".join(p for k, p in _png_chunks(data) if k == "IDAT")
    try:
        raw = zlib.decompress(idat)
    except zlib.error as exc:
        raise ImageError("corrupt PNG image data: %s" % exc)
    ch = _PNG_CHANNELS[info.color_type]
    return Pixels(info.width, info.height, ch, _unfilter(raw, info.width, info.height, ch))


def encode_png(width: int, height: int, channels: int, data: bytes) -> bytes:
    """Minimal 8-bit PNG writer (filter 0). For tests and generated fixtures."""
    ctype = {1: 0, 2: 4, 3: 2, 4: 6}[channels]
    stride = width * channels
    if len(data) != stride * height:
        raise ValueError("pixel data does not match %dx%dx%d" % (width, height, channels))
    raw = b"".join(b"\x00" + data[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + \
            struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    return (PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, ctype, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def sample_grid(px: Pixels, size: int) -> List[List[tuple]]:
    """A size x size nearest-neighbour grid of RGB values (for coarse image checks)."""
    return [[px.rgb(min(px.width - 1, (x * px.width) // size), min(px.height - 1, (y * px.height) // size))
             for x in range(size)] for y in range(size)]
