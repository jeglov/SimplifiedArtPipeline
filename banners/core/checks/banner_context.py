"""What a banner check sees, and the shared helpers (rectangles, WCAG contrast, pixel sampling).

Two sources, never the encoder's input alone:
* `layout`: the page's own measurements per frame (render.mjs + page.py):
  transformed boxes, drawn content, opacity, line counts, fonts used.
* pixels: frames decoded back from banner.mp4 (`decoded`), and the
  text-hidden render of the same frames (`background`) for contrast.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from banners.core.rules import BannerRules, Placement
from banners.core.template import Template
from validate.core.image import Pixels, decode_png

Rect = Tuple[float, float, float, float]   # left, top, right, bottom in px


@dataclass
class BannerContext:
    template: Template
    placement: Placement
    rules: BannerRules
    layout: Mapping[str, Any]               # banners/build.py layout.json
    facts: Mapping[str, Any]                # banners.core.video.facts of banner.mp4
    decoded: Mapping[int, Path]             # frame -> PNG decoded from banner.mp4
    background: Mapping[int, Path]          # frame -> rendered PNG with text and buttons hidden
    _pixels: Dict[Path, Pixels] = field(default_factory=dict)

    @property
    def fps(self) -> int:
        return int(self.layout["page"]["fps"])

    def frames(self) -> List[int]:
        return sorted(int(k) for k in self.layout["frames"])

    def state(self, frame: int, layer: str) -> Mapping[str, Any]:
        return self.layout["frames"][str(frame)][layer]

    def pixels(self, path: Path) -> Pixels:
        """Decoded once per banner: ~2 s for a 1080x1920 PNG in pure Python."""
        if path not in self._pixels:
            self._pixels[path] = decode_png(path)
        return self._pixels[path]


def inside(r: Sequence[float], zone: Sequence[float], tol: float) -> bool:
    return r[0] >= zone[0] - tol and r[1] >= zone[1] - tol and r[2] <= zone[2] + tol and r[3] <= zone[3] + tol


def outside_by(r: Sequence[float], zone: Sequence[float]) -> Dict[str, float]:
    """How far each edge of r pokes out of zone (only the edges that do)."""
    out = {"left": zone[0] - r[0], "top": zone[1] - r[1], "right": r[2] - zone[2], "bottom": r[3] - zone[3]}
    return {k: round(v, 2) for k, v in out.items() if v > 0}


def hex_rgb(value: str) -> Tuple[int, int, int]:
    v = value.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def _linear(c: int) -> float:
    s = c / 255.0
    return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4


def luminance(rgb: Sequence[int]) -> float:
    """WCAG 2.x relative luminance of an sRGB colour."""
    r, g, b = (_linear(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: Sequence[int], b: Sequence[int]) -> float:
    """WCAG contrast ratio, 1 (same) to 21 (black on white)."""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def sample(px: Pixels, rect: Sequence[float], step: int = 4) -> List[Tuple[int, int, int]]:
    """RGB samples on a `step`-px grid inside rect (clipped to the image)."""
    x0, y0 = max(0, int(rect[0])), max(0, int(rect[1]))
    x1, y1 = min(px.width, int(rect[2])), min(px.height, int(rect[3]))
    return [px.rgb(x, y) for y in range(y0, y1, step) for x in range(x0, x1, step)]


def percentile(values: Iterable[float], share: float) -> float:
    s = sorted(values)
    if not s:
        return 0.0
    return s[min(len(s) - 1, int(share * len(s)))]


def readable_layers(template: Template) -> List[str]:
    return [layer.id for layer in template.layers if layer.readable]


def contrast_frame(ctx: BannerContext, layer: str) -> Optional[int]:
    """The key frame in the middle of the layer's longest fully-visible time (where it is measured)."""
    from banners.core.page import frame_for, visible_spans
    spans = visible_spans(ctx.template.layer(layer), ctx.template.duration_s)
    if not spans:
        return None
    a, b = max(spans, key=lambda s: s[1] - s[0])
    return frame_for((a + b) / 2.0, ctx.fps, len(ctx.layout["frames"]))
