"""Template + kit + placement + locale -> one HTML page and its files, ready for render.mjs.

Everything that decides a pixel is computed here, in Python:

* every layer's box in pixels, every image's fitted rectangle (contain /
  cover, from the PNG header), font sizes, colours, the text;
* the state of every layer at every frame (banners/core/timeline.py),
  embedded as a table. The page's apply(n) only copies numbers from it.

The page reports back what only a layout engine knows: per layer and per
frame the transformed bounding box (`box`) and the drawn content (`content`:
the text's line boxes, the image's rectangle), and once per page each text's
line count and whether it overflows its area. The checks read that record.

DOM per layer:  div.layer#<id> (box, transform, opacity)
                  image:        img
                  text/button:  div.area (button: inset by half the height) > span.t
Text never breaks inside a word (overflow-wrap: normal), so a word too long
for its box overflows visibly and measurably instead of being split.

Pure Python 3.9.
"""
from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from banners.core import timeline as tl
from banners.core.kit import Font, Kit
from banners.core.rules import Placement
from banners.core.template import Layer, Template
from validate.core.image import read_info

ALIGN = {"left": "flex-start", "center": "center", "right": "flex-end"}


@dataclass(frozen=True)
class Overrides:
    """What the repair step may change: per layer a font scale, per colour slot a palette name."""
    font_scale: Mapping[str, float] = field(default_factory=dict)
    colours: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Page:
    html: str
    files: Dict[str, Path]          # served path -> local file (index.html added by the caller)
    frames: int
    fps: int
    text_layers: Tuple[str, ...]
    colours: Dict[str, str]         # colour slot -> hex actually used (after overrides)
    font_px: Dict[str, float]       # text layer -> font size in px (after overrides)


def _px(v: float) -> str:
    return ("%.2f" % v).rstrip("0").rstrip(".") + "px"


def fitted(fit: str, bw: float, bh: float, iw: int, ih: int) -> Tuple[float, float, float, float]:
    """(x, y, w, h) of the image inside its box, centred: contain fits inside, cover fills (and is clipped)."""
    s = max(bw / iw, bh / ih) if fit == "cover" else min(bw / iw, bh / ih)
    w, h = iw * s, ih * s
    return (bw - w) / 2.0, (bh - h) / 2.0, w, h


def colour_map(template: Template, kit: Kit, overrides: Overrides) -> Dict[str, str]:
    out = {}
    for slot in template.slots["colour"]:
        name = overrides.colours.get(slot, kit.colours.get(slot))
        out[slot] = kit.palette[name]
    return out


def _shadow(colour: Optional[str]) -> str:
    # A hard drop (reads as "game UI") plus a soft halo that lifts text off a busy background.
    if not colour:
        return "none"
    return "0 0.06em 0 %s, 0 0 0.22em %s" % (colour, colour)


def _layer_html(layer: Layer, p: Placement, kit: Kit, fonts: Mapping[str, Font], colours: Mapping[str, str],
                strings: Mapping[str, str], font_px: Dict[str, float], files: Dict[str, Path],
                overrides: Overrides) -> str:
    x, y, w, h = layer.box[0] * p.width, layer.box[1] * p.height, layer.box[2] * p.width, layer.box[3] * p.height
    style = "left:%s;top:%s;width:%s;height:%s;" % (_px(x), _px(y), _px(w), _px(h))
    if layer.kind == "image":
        img = kit.images[layer.image]
        info = read_info(img.path)
        served = "images/%s.png" % layer.image
        files[served] = img.path
        ix, iy, iw, ih = fitted(layer.fit, w, h, info.width, info.height)
        clip = "overflow:hidden;" if layer.fit == "cover" else ""
        return ('<div class="layer" id="%s" data-kind="image" style="%s%s"><img src="%s" alt="" '
                'width="%d" height="%d" style="left:%s;top:%s;width:%s;height:%s;"></div>'
                % (layer.id, style, clip, served, info.width, info.height, _px(ix), _px(iy), _px(iw), _px(ih)))
    size = layer.size * p.height * overrides.font_scale.get(layer.id, 1.0)
    font_px[layer.id] = round(size, 2)
    inset = h / 2.0 if layer.kind == "button" else 0.0
    if layer.kind == "button":
        style += "background:%s;border-radius:%s;" % (colours[layer.fill], _px(layer.radius * h))
    text_style = ("font-family:'artpipe-%s';font-weight:%d;font-size:%s;line-height:%s;color:%s;"
                  "text-align:%s;text-shadow:%s;" % (layer.font, layer.weight, _px(size), layer.line_height,
                                                     colours[layer.colour], layer.align,
                                                     _shadow(colours.get(layer.shadow) if layer.shadow else None)))
    return ('<div class="layer" id="%s" data-kind="%s" style="%s"><div class="area" style="left:%s;right:%s;'
            'justify-content:%s;"><span class="t" style="%s" data-lh="%s">%s</span></div></div>'
            % (layer.id, layer.kind, style, _px(inset), _px(inset), ALIGN[layer.align], text_style,
               _px(size * layer.line_height), html.escape(strings[layer.text])))


SCRIPT = """
const S = JSON.parse(document.getElementById("states").textContent);
const IDS = S.ids, W = S.width, H = S.height;
const els = IDS.map((id) => document.getElementById(id));
const r2 = (v) => Math.round(v * 100) / 100;
const rect = (r) => [r2(r.left), r2(r.top), r2(r.right), r2(r.bottom)];
function content(el) {
  const t = el.querySelector(".t");
  if (t) return rect(t.getBoundingClientRect());
  const img = el.querySelector("img");
  return img ? rect(img.getBoundingClientRect()) : rect(el.getBoundingClientRect());
}
window.artpipe = {
  ready() {
    const text = {};
    for (const el of els) {
      const t = el.querySelector(".t");
      if (!t) continue;
      const area = el.querySelector(".area");
      const lh = parseFloat(t.dataset.lh);
      text[el.id] = { lines: Math.round(t.offsetHeight / lh), width: t.offsetWidth, height: t.offsetHeight,
                      area: [area.clientWidth, area.clientHeight] };
    }
    const images = {};
    for (const el of els) {
      const img = el.querySelector("img");
      if (img) images[el.id] = { natural: [img.naturalWidth, img.naturalHeight], drawn: [img.offsetWidth, img.offsetHeight] };
    }
    return { text: Object.fromEntries(Object.keys(text).map((id) => [id, "#" + id + " .t"])), measure: { text, images } };
  },
  apply(n) {
    const f = S.frames[n], out = {};
    for (let i = 0; i < els.length; i++) {
      const [o, s, x, y, r] = f[i], el = els[i];
      el.style.opacity = o;
      el.style.transform = `translate(${x * W}px, ${y * H}px) rotate(${r}deg) scale(${s})`;
      out[el.id] = { o, box: rect(el.getBoundingClientRect()), content: content(el) };
    }
    return out;
  },
  hideText(on) {
    for (const el of els) if (el.dataset.kind !== "image") el.style.visibility = on ? "hidden" : "";
  },
};
"""


def build(template: Template, kit: Kit, fonts: Mapping[str, Font], p: Placement, locale: str,
          overrides: Optional[Overrides] = None) -> Page:
    overrides = overrides or Overrides()
    colours = colour_map(template, kit, overrides)
    strings = kit.strings[locale]
    files: Dict[str, Path] = {}
    faces = []
    for slot in template.slots["font"]:
        font = fonts[kit.fonts[slot]]
        served = "fonts/%s.ttf" % slot
        files[served] = font.path
        faces.append("@font-face{font-family:'artpipe-%s';src:url('%s');font-weight:%d %d;}"
                     % (slot, served, font.weights[0], font.weights[1]))
    font_px: Dict[str, float] = {}
    layers = "\n".join(_layer_html(layer, p, kit, fonts, colours, strings, font_px, files, overrides)
                       for layer in template.layers)
    tracks = {layer.id: layer.tracks for layer in template.layers}
    states = tl.states(tracks, template.duration_s, p.fps)
    ids = [layer.id for layer in template.layers]
    table = {"ids": ids, "width": p.width, "height": p.height,
             "frames": [[[s[i]["opacity"], s[i]["scale"], s[i]["x"], s[i]["y"], s[i]["rotate"]] for i in ids]
                        for s in states]}
    doc = """<!doctype html>
<html lang="%(lang)s"><head><meta charset="utf-8"><title>%(title)s</title>
<style>
%(faces)s
html,body{margin:0;width:%(w)dpx;height:%(h)dpx;overflow:hidden;background:#000;}
.layer{position:absolute;transform-origin:50%% 50%%;will-change:auto;}
.layer img{position:absolute;display:block;}
.area{position:absolute;top:0;bottom:0;display:flex;align-items:center;}
.t{display:block;overflow-wrap:normal;word-break:normal;hyphens:manual;white-space:normal;}
</style></head>
<body>
%(layers)s
<script type="application/json" id="states">%(states)s</script>
<script>%(script)s</script>
</body></html>
""" % {"lang": html.escape(locale), "title": html.escape("%s %s %s" % (kit.id, template.id, p.id)),
       "faces": "\n".join(faces), "w": p.width, "h": p.height, "layers": layers,
       "states": json.dumps(table, separators=(",", ":")).replace("</", "<\\/"), "script": SCRIPT}
    return Page(doc, files, len(states), p.fps, tuple(layer.id for layer in template.layers
                                                       if layer.kind != "image"), colours, font_px)


def frame_for(t: float, fps: int, frames: int) -> int:
    """The frame showing time t (clamped to the last frame)."""
    return max(0, min(frames - 1, int(round(t * fps))))


def visible_spans(layer: Layer, duration: float) -> List[Tuple[float, float]]:
    """Intervals where the layer is fully opaque (no opacity track: the whole banner)."""
    track = layer.tracks.get("opacity")
    if not track:
        return [(0.0, duration)]
    spans, start = [], None
    steps = int(round(duration * 100))
    for i in range(steps + 1):
        t = i / 100.0
        on = tl.value_at(track, t) >= 0.999
        if on and start is None:
            start = t
        if not on and start is not None:
            spans.append((start, t))
            start = None
    if start is not None:
        spans.append((start, duration))
    return spans


def key_frames(template: Template, fps: int, frames: int) -> List[int]:
    """Frames a human and the pixel checks look at: the first and last frame, every whole second, and the
    middle of each text, button or logo layer's fully visible time (where its contrast is measured)."""
    times = {0.0, template.duration_s} | {float(s) for s in range(1, int(template.duration_s))}
    for layer in template.layers:
        if layer.readable:
            for a, b in visible_spans(layer, template.duration_s):
                times.add((a + b) / 2.0)
    return sorted({frame_for(t, fps, frames) for t in times})
