"""Hero art from a Phase 3 concept: the image + its BiRefNet mask -> a cropped RGBA PNG.

The concept stage already guarantees one whole object on a plain backdrop
(concept.* checks), and the mask is the one image-to-3D used, so the cut-out
is the object the game's 3D asset was built from.

Two details decide whether it looks cut out by a professional or by a
magic wand:

* Alpha from the mask through a ramp (`lo`..`hi`): BiRefNet's soft edge
  is kept as a 1-3 px antialiased edge, and faint mask noise (<lo) drops out.
* Edge decontamination: an edge pixel is a mix of the object and the grey
  backdrop, I = a*C + (1-a)*B. Shown on a green forest, the grey would be a
  halo. Solving for C (B = the backdrop colour, the median of the image
  border) removes it.

Pure Python 3.9 (~3 s for a 1024x1024 concept on mayapy).
"""
from __future__ import annotations

from typing import Tuple

from validate.core.image import Pixels


def _median(values):
    s = sorted(values)
    return s[len(s) // 2]


def backdrop(image: Pixels) -> Tuple[int, int, int]:
    """Median colour of the 1-px image border, per channel."""
    w, h = image.width, image.height
    ring = [image.rgb(x, 0) for x in range(w)] + [image.rgb(x, h - 1) for x in range(w)]
    ring += [image.rgb(0, y) for y in range(h)] + [image.rgb(w - 1, y) for y in range(h)]
    return tuple(_median([p[c] for p in ring]) for c in range(3))


def bbox(mask: Pixels, threshold: int):
    """(x0, y0, x1, y1) exclusive of the mask cells >= threshold, or None if empty."""
    xs, ys = [], []
    for y in range(mask.height):
        row = mask.data[y * mask.width * mask.channels:(y + 1) * mask.width * mask.channels:mask.channels]
        hits = [x for x, v in enumerate(row) if v >= threshold]
        if hits:
            xs += (hits[0], hits[-1])
            ys.append(y)
    if not ys:
        return None
    return min(xs), ys[0], max(xs) + 1, ys[-1] + 1


def crop_to_alpha(rgba: Pixels, margin_px: int = 4) -> Tuple[int, int, bytes]:
    """An RGBA image cropped to its non-transparent pixels plus a margin (logo art from a page render)."""
    if rgba.channels != 4:
        raise ValueError("crop_to_alpha needs RGBA, got %d channels" % rgba.channels)
    alpha = Pixels(rgba.width, rgba.height, 1, rgba.data[3::4])
    box = bbox(alpha, 1)
    if box is None:
        raise ValueError("the image is fully transparent")
    x0, y0 = max(0, box[0] - margin_px), max(0, box[1] - margin_px)
    x1, y1 = min(rgba.width, box[2] + margin_px), min(rgba.height, box[3] + margin_px)
    stride = rgba.width * 4
    rows = [rgba.data[y * stride + x0 * 4:y * stride + x1 * 4] for y in range(y0, y1)]
    return x1 - x0, y1 - y0, b"".join(rows)


def cutout(image: Pixels, mask: Pixels, lo: int = 16, hi: int = 240, margin: float = 0.03
           ) -> Tuple[int, int, bytes]:
    """(width, height, RGBA bytes) of the object, cropped to its mask with a margin (share of the larger side)."""
    if (image.width, image.height) != (mask.width, mask.height):
        raise ValueError("image %dx%d and mask %dx%d differ" % (image.width, image.height, mask.width, mask.height))
    if not 0 <= lo < hi <= 255:
        raise ValueError("need 0 <= lo < hi <= 255")
    box = bbox(mask, lo + 1)   # alpha > 0 exactly when m > lo
    if box is None:
        raise ValueError("the mask is empty")
    pad = int(round(margin * max(box[2] - box[0], box[3] - box[1])))
    x0, y0 = max(0, box[0] - pad), max(0, box[1] - pad)
    x1, y1 = min(image.width, box[2] + pad), min(image.height, box[3] + pad)
    bg = backdrop(image)
    span = float(hi - lo)
    out = bytearray()
    for y in range(y0, y1):
        for x in range(x0, x1):
            m = mask.data[(y * mask.width + x) * mask.channels]
            a = 0.0 if m <= lo else 1.0 if m >= hi else (m - lo) / span
            if a == 0.0:
                out += b"\x00\x00\x00\x00"
                continue
            px = image.rgb(x, y)
            if a < 1.0:
                px = tuple(min(255, max(0, int(round((px[c] - (1.0 - a) * bg[c]) / a)))) for c in range(3))
            out += bytes((px[0], px[1], px[2], int(round(a * 255))))
    return x1 - x0, y1 - y0, bytes(out)
