"""A game kit (banners/kits/<game>/kit.yaml): what one game puts into every banner.

    game:      {id, name, fictional}
    palette:   {name: "#RRGGBB"}       the game's colours (repair may pick among them)
    colours:   {slot: palette name}    which palette colour fills each colour slot
    fonts:     {slot: font id}         ids from banners/fonts/fonts.yaml
    images:    {slot: {file, licence}}     + <file>.provenance.json next to each image
    strings:   {locale: {slot: text}}

Every image has a provenance sidecar, <file>.provenance.json, written by
`artpipe banners art` (banners/art.py): the file's sha256, how it was made
(a ComfyUI run with its models and licences, a cut-out of a concept, a page
rendered from a source file) and from what. The kit names the file and its
licence; the sidecar proves the rest, so regenerating art never means
editing kit.yaml by hand.

This module reads the STRUCTURE strictly (a malformed kit is a tool error,
exit 2). Whether the content is right (every slot filled, hashes, licences,
image sizes) is judged by the kit.* checks (banners/core/checks/kit.py), so
an agent gets a report it can act on, not a traceback.

Pure Python 3.9.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from validate.core.conventions import ConventionError, _read_yaml, _Reader

KIT_DIR = Path(__file__).resolve().parents[1] / "kits"
FONT_DIR = Path(__file__).resolve().parents[1] / "fonts"
FONTS_FILE = "fonts.yaml"
_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_LOCALE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")


@dataclass(frozen=True)
class Font:
    id: str
    family: str
    path: Path
    sha256: str
    weights: Tuple[int, int]
    licence: str
    licence_file: Path
    source: str


@dataclass(frozen=True)
class Image:
    slot: str
    path: Path
    licence: str
    provenance: Path              # <file>.provenance.json
    record: Optional[Dict]        # the sidecar's JSON, None if missing or unreadable

    @property
    def sha256(self) -> Optional[str]:
        return (self.record or {}).get("output_sha256")


def provenance_path(image: Path) -> Path:
    return image.with_name(image.name + ".provenance.json")


def read_record(path: Path) -> Optional[Dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@dataclass(frozen=True)
class Kit:
    id: str
    name: str
    fictional: bool
    path: Path                    # the kit folder
    palette: Dict[str, str]
    colours: Dict[str, str]       # slot -> palette name
    fonts: Dict[str, str]         # slot -> font id
    images: Dict[str, Image]
    strings: Dict[str, Dict[str, str]]

    def colour(self, slot: str) -> Optional[str]:
        name = self.colours.get(slot)
        return self.palette.get(name) if name else None


def parse_fonts(data: dict, folder: Path = FONT_DIR) -> Dict[str, Font]:
    root = _Reader(data, FONTS_FILE)
    root.get("version", int)
    fonts = {}
    for fid, raw in sorted(root.get("fonts", dict).items()):
        if not _ID.match(str(fid)):
            raise ConventionError("%s.fonts.%s: font id must be snake_case" % (FONTS_FILE, fid))
        r = _Reader(raw, "%s.fonts.%s" % (FONTS_FILE, fid))
        weights = r.get("weights", list)
        if len(weights) != 2 or not all(isinstance(w, int) and not isinstance(w, bool) for w in weights):
            raise ConventionError("%s.weights: expected [min, max]" % r.where)
        fonts[fid] = Font(fid, r.get("family", str), folder / r.get("file", str), r.get("sha256", str),
                          (weights[0], weights[1]), r.get("licence", str), folder / r.get("licence_file", str),
                          r.get("source", str))
        r.done()
    root.done()
    return fonts


def load_fonts(folder: Path = FONT_DIR) -> Dict[str, Font]:
    return parse_fonts(_read_yaml(folder / FONTS_FILE), folder)


def _names(r: _Reader, key: str, value_pattern=None) -> Dict[str, str]:
    raw = r.get(key, dict, required=False, default={})
    out = {}
    for k, v in sorted(raw.items()):
        if not isinstance(k, str) or not _ID.match(k) or not isinstance(v, str):
            raise ConventionError("%s.%s.%s: expected snake_case key and a string value" % (r.where, key, k))
        out[k] = v
    return out


def parse(data: dict, folder: Path) -> Kit:
    where = "%s/kit.yaml" % folder.name
    root = _Reader(data, where)
    root.get("version", int)
    g = root.sub("game")
    kid = g.get("id", str)
    if kid != folder.name or not _ID.match(kid):
        raise ConventionError("%s.game.id: %r must be snake_case and match the folder name" % (where, kid))
    name, fictional = g.get("name", str), g.get("fictional", bool)
    g.done()
    images = {}
    for slot, raw in sorted(root.get("images", dict, required=False, default={}).items()):
        r = _Reader(raw, "%s.images.%s" % (where, slot))
        path = folder / r.get("file", str)
        images[slot] = Image(slot, path, r.get("licence", str), provenance_path(path),
                             read_record(provenance_path(path)))
        r.done()
    strings = {}
    for locale, raw in sorted(root.get("strings", dict, required=False, default={}).items()):
        if not isinstance(locale, str) or not _LOCALE.match(locale):
            raise ConventionError("%s.strings.%s: a locale like en or pt-BR" % (where, locale))
        strings[locale] = _names(_Reader({"s": raw}, "%s.strings" % where), "s")
    kit = Kit(kid, name, fictional, folder, _names(root, "palette"), _names(root, "colours"),
              _names(root, "fonts"), images, strings)
    root.done()
    return kit


def load(kit: str) -> Kit:
    """A kit id (banners/kits/<id>/) or a kit folder."""
    folder = Path(kit)
    if not (folder / "kit.yaml").is_file():
        folder = KIT_DIR / kit
    if not (folder / "kit.yaml").is_file():
        raise ConventionError("kit not found: %s (kits: %s)" % (kit, sorted(p.parent.name for p in KIT_DIR.glob("*/kit.yaml"))))
    return parse(_read_yaml(folder / "kit.yaml"), folder.resolve())
