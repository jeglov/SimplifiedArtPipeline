"""Does a background work behind this template? Two measures, before anything is rendered.

What the first Spotcap background got wrong, seen by eye in step 4.4 and
measured here:

* clutter: mean luminance step between neighbouring samples (every 8 px of
  the frame) inside the text boxes. Busy detail behind text (leaves, light
  specks) makes letters hard to read even when the average contrast passes.
* hero clash: in a ring around the hero's box, the share of saturated
  background pixels whose hue is within `HUE_TOLERANCE` degrees of the
  hero's dominant hue. A red mushroom hero in front of red mushrooms has no
  figure-ground: the eye doesn't find it.

The background is placed exactly as the page draws it (cover, centred), at
the start of its animation (scale track's first value). Pure Python 3.9.
"""
from __future__ import annotations

import colorsys
from typing import Dict, List, Optional, Sequence, Tuple

from banners.core.page import fitted
from validate.core.image import Pixels

STEP = 8
HUE_BINS = 18                 # 20 degrees each
HUE_TOLERANCE = 20.0
SATURATED = 0.35              # HSV saturation and value floors for "a colour", not grey or shadow
VISIBLE = 0.25
RING = 0.15                   # the ring around the hero box, as a share of the box size


def _hsv(rgb):
    return colorsys.rgb_to_hsv(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)


def coloured(rgb) -> Optional[float]:
    """Hue in degrees of a saturated, visible colour; None for greys, darks and pale tints."""
    h, s, v = _hsv(rgb)
    return h * 360.0 if s >= SATURATED and v >= VISIBLE else None


def dominant_hue(image: Pixels, step: int = 4) -> Optional[float]:
    """Centre of the most common 20-degree hue bin among the opaque, saturated pixels (hero art)."""
    bins = [0] * HUE_BINS
    for y in range(0, image.height, step):
        for x in range(0, image.width, step):
            if image.alpha(x, y) < 128:
                continue
            hue = coloured(image.rgb(x, y))
            if hue is not None:
                bins[int(hue // (360.0 / HUE_BINS)) % HUE_BINS] += 1
    if not any(bins):
        return None
    best = max(range(HUE_BINS), key=lambda i: (bins[i], -i))
    return (best + 0.5) * (360.0 / HUE_BINS)


def _hue_gap(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


class Placed:
    """The background as the frame sees it: frame (x, y) -> image pixel, for a cover fit at a scale."""

    def __init__(self, image: Pixels, width: int, height: int, scale: float = 1.0):
        ix, iy, iw, ih = fitted("cover", width, height, image.width, image.height)
        cx, cy = width / 2.0, height / 2.0
        self.image = image
        self.x0, self.y0 = cx + (ix - cx) * scale, cy + (iy - cy) * scale
        self.sx, self.sy = image.width / (iw * scale), image.height / (ih * scale)

    def rgb(self, x: float, y: float) -> Tuple[int, int, int]:
        px = min(self.image.width - 1, max(0, int((x - self.x0) * self.sx)))
        py = min(self.image.height - 1, max(0, int((y - self.y0) * self.sy)))
        return self.image.rgb(px, py)


def _lum(rgb) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def clutter(bg: Placed, boxes: Sequence[Tuple[float, float, float, float]]) -> float:
    """Mean luminance step (0-255) between horizontal and vertical neighbours sampled every STEP px."""
    steps: List[float] = []
    for x0, y0, x1, y1 in boxes:
        y = y0
        while y + STEP <= y1:
            x = x0
            while x + STEP <= x1:
                here = _lum(bg.rgb(x, y))
                steps.append(abs(here - _lum(bg.rgb(x + STEP, y))))
                steps.append(abs(here - _lum(bg.rgb(x, y + STEP))))
                x += STEP
            y += STEP
    return round(sum(steps) / len(steps), 2) if steps else 0.0


def clash(bg: Placed, hero_box: Tuple[float, float, float, float], hue: float) -> Dict[str, float]:
    """Share of the ring around the hero box whose colour has the hero's hue."""
    x0, y0, x1, y1 = hero_box
    dx, dy = (x1 - x0) * RING, (y1 - y0) * RING
    outer = (x0 - dx, y0 - dy, x1 + dx, y1 + dy)
    total = same = 0
    y = outer[1]
    while y < outer[3]:
        x = outer[0]
        while x < outer[2]:
            if not (x0 <= x < x1 and y0 <= y < y1):
                total += 1
                h = coloured(bg.rgb(x, y))
                if h is not None and _hue_gap(h, hue) <= HUE_TOLERANCE:
                    same += 1
            x += STEP
        y += STEP
    return {"ring_samples": total, "same_hue_share": round(same / float(total), 4) if total else 0.0}
