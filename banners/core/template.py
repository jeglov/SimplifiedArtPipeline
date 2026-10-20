"""banners/templates/<id>.yaml as typed, immutable data.

A template is layers (image, text, button) with boxes as shares of the frame,
keyframe tracks, and the slots a game kit must fill. It never names a colour
value, a font file or a string: those come from the kit. Strict reader: an
unknown key, an undeclared slot or keys out of order fail at load time.

Pure Python 3.9.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from banners.core import timeline as tl
from validate.core.conventions import ConventionError, _read_yaml, _Reader

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
_ID = re.compile(r"^[a-z][a-z0-9_]*$")
SLOT_KINDS = ("colour", "font", "image", "text")
LAYER_KINDS = ("image", "text", "button")
FITS = ("cover", "contain")
ALIGNS = ("left", "center", "right")


@dataclass(frozen=True)
class Layer:
    id: str
    kind: str                         # image | text | button
    box: Tuple[float, float, float, float]   # x, y, w, h as shares of the frame
    tracks: Dict[str, tl.Track]
    image: Optional[str] = None       # image slot (kind image)
    fit: str = "contain"
    text: Optional[str] = None        # text slot (text, button)
    font: Optional[str] = None        # font slot
    weight: int = 400
    colour: Optional[str] = None      # colour slot of the text
    shadow: Optional[str] = None      # colour slot of a text shadow (readability on images)
    fill: Optional[str] = None        # colour slot of a button
    size: float = 0.0                 # font size, share of the frame height
    line_height: float = 1.2
    max_lines: int = 1
    align: str = "center"
    radius: float = 0.0               # button corner radius, share of the button height

    @property
    def readable(self) -> bool:
        """Text, buttons and the logo must stay inside the safe zone; backgrounds and heroes may bleed."""
        return self.kind in ("text", "button") or self.image == "logo"


@dataclass(frozen=True)
class Template:
    id: str
    path: Path
    description: str
    duration_s: float
    end_card_from_s: float
    slots: Dict[str, Tuple[str, ...]]     # kind -> slot names
    layers: Tuple[Layer, ...]

    def layer(self, lid: str) -> Layer:
        return next(layer for layer in self.layers if layer.id == lid)


def _slot(r: _Reader, key: str, kind: str, slots: Dict[str, Tuple[str, ...]], required: bool = True) -> Optional[str]:
    val = r.get(key, str, required=required)
    if val is not None and val not in slots[kind]:
        raise ConventionError("%s.%s: %r is not a declared %s slot %s" % (r.where, key, val, kind, list(slots[kind])))
    return val


def _tracks(r: _Reader, duration: float) -> Dict[str, tl.Track]:
    out = {}
    for prop in sorted(r.data):
        where = "%s.%s" % (r.where, prop)
        if prop not in tl.PROPERTIES:
            raise ConventionError("%s: unknown property, expected one of %s" % (where, sorted(tl.PROPERTIES)))
        raw = r.get(prop, list)
        if not raw:
            raise ConventionError("%s: needs at least one key" % where)
        keys = []
        for i, k in enumerate(raw):
            if not isinstance(k, list) or len(k) not in (2, 3) or any(isinstance(v, bool) for v in k[:2]) \
                    or not all(isinstance(v, (int, float)) for v in k[:2]):
                raise ConventionError("%s[%d]: a key is [time_s, value] or [time_s, value, easing]" % (where, i))
            easing = k[2] if len(k) == 3 else "linear"
            if easing not in tl.EASINGS:
                raise ConventionError("%s[%d]: unknown easing %r, expected one of %s"
                                      % (where, i, easing, sorted(tl.EASINGS)))
            if not 0.0 <= k[0] <= duration:
                raise ConventionError("%s[%d]: time %s outside 0-%s s" % (where, i, k[0], duration))
            if keys and k[0] <= keys[-1].t:
                raise ConventionError("%s[%d]: key times must increase (%s after %s)" % (where, i, k[0], keys[-1].t))
            keys.append(tl.Key(float(k[0]), float(k[1]), easing))
        out[prop] = tuple(keys)
    return out


def _box(r: _Reader, readable: bool) -> Tuple[float, float, float, float]:
    raw = r.get("box", list)
    if len(raw) != 4 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in raw):
        raise ConventionError("%s.box: expected [x, y, w, h]" % r.where)
    x, y, w, h = (float(v) for v in raw)
    if w <= 0 or h <= 0:
        raise ConventionError("%s.box: width and height must be > 0" % r.where)
    if readable and (x < 0 or y < 0 or x + w > 1.0 + 1e-9 or y + h > 1.0 + 1e-9):
        raise ConventionError("%s.box: a text, button or logo box must lie inside the frame" % r.where)
    return x, y, w, h


def _layer(r: _Reader, slots: Dict[str, Tuple[str, ...]], duration: float) -> Layer:
    lid = r.get("id", str)
    if not _ID.match(lid):
        raise ConventionError("%s.id: layer id must be snake_case" % r.where)
    kind = r.get("kind", str)
    if kind not in LAYER_KINDS:
        raise ConventionError("%s.kind: expected one of %s" % (r.where, list(LAYER_KINDS)))
    tracks = _tracks(_Reader(r.get("tracks", dict, required=False, default={}), r.where + ".tracks"), duration)
    if kind == "image":
        image = _slot(r, "image", "image", slots)
        fit = r.get("fit", str, required=False, default="contain")
        if fit not in FITS:
            raise ConventionError("%s.fit: expected one of %s" % (r.where, list(FITS)))
        layer = Layer(lid, kind, _box(r, image == "logo"), tracks, image=image, fit=fit)
    else:
        size = r.get("size", float)
        if not 0.0 < size < 0.5:
            raise ConventionError("%s.size: a share of the frame height in (0, 0.5)" % r.where)
        max_lines = r.get("max_lines", int)
        if max_lines < 1:
            raise ConventionError("%s.max_lines: must be >= 1" % r.where)
        align = r.get("align", str, required=False, default="center")
        if align not in ALIGNS:
            raise ConventionError("%s.align: expected one of %s" % (r.where, list(ALIGNS)))
        weight = r.get("weight", int, required=False, default=400)
        if weight % 100 or not 100 <= weight <= 900:
            raise ConventionError("%s.weight: 100-900 in steps of 100" % r.where)
        layer = Layer(lid, kind, _box(r, True), tracks, text=_slot(r, "text", "text", slots),
                      font=_slot(r, "font", "font", slots), weight=weight, colour=_slot(r, "colour", "colour", slots),
                      shadow=_slot(r, "shadow", "colour", slots, required=False),
                      fill=_slot(r, "fill", "colour", slots, required=kind == "button"),
                      size=size, line_height=r.get("line_height", float, required=False, default=1.2),
                      max_lines=max_lines, align=align,
                      radius=r.get("radius", float, required=False, default=0.0))
    r.done()
    return layer


def parse(data: dict, path: Path) -> Template:
    where = path.name
    root = _Reader(data, where)
    root.get("version", int)
    tid = root.get("id", str)
    if tid != path.stem:
        raise ConventionError("%s.id: %r must match the file name" % (where, tid))
    duration = root.get("duration_s", float)
    if duration <= 0:
        raise ConventionError("%s.duration_s: must be > 0" % where)
    end_card = root.get("end_card_from_s", float)
    if not 0.0 <= end_card < duration:
        raise ConventionError("%s.end_card_from_s: must be inside the duration" % where)
    rs = root.sub("slots")
    slots = {}
    for kind in SLOT_KINDS:
        names = rs.get(kind, list, required=False, default=[])
        if len(set(names)) != len(names) or not all(isinstance(n, str) and _ID.match(n) for n in names):
            raise ConventionError("%s.slots.%s: unique snake_case names" % (where, kind))
        slots[kind] = tuple(names)
    rs.done()
    raw_layers = root.get("layers", list)
    layers = tuple(_layer(_Reader(raw, "%s.layers[%d]" % (where, i)), slots, duration)
                   for i, raw in enumerate(raw_layers))
    ids = [layer.id for layer in layers]
    if not layers or len(set(ids)) != len(ids):
        raise ConventionError("%s.layers: at least one layer, unique ids" % where)
    used = {(k, getattr(layer, attr)) for layer in layers
            for k, attr in (("image", "image"), ("text", "text"), ("font", "font"), ("colour", "colour"),
                            ("colour", "shadow"), ("colour", "fill")) if getattr(layer, attr)}
    unused = sorted("%s.%s" % (k, n) for k in SLOT_KINDS for n in slots[k] if (k, n) not in used)
    if unused:
        raise ConventionError("%s.slots: declared but no layer uses %s" % (where, unused))
    root.get("description", str)
    root.done()
    return Template(tid, path, root.data["description"], duration, end_card, slots, layers)


def load(template: str) -> Template:
    """A template id (banners/templates/<id>.yaml) or a path."""
    path = Path(template)
    if not path.suffix:
        path = TEMPLATE_DIR / (template + ".yaml")
    if not path.is_file():
        raise ConventionError("template not found: %s (templates: %s)"
                              % (template, sorted(p.stem for p in TEMPLATE_DIR.glob("*.yaml"))))
    return parse(_read_yaml(path), path)
