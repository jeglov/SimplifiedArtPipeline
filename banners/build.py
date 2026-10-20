"""Render one banner: template x kit x placement x locale -> MP4 + layout record + contact sheet.

    out/banners/<kit>/<template>/<placement>/<locale>/
        banner.mp4         the deliverable
        layout.json        the page's measurements per frame + what was rendered (fonts, colours, sizes)
        page/index.html    the generated page (open it in a browser to debug)
        frames/            rendered key frames (f*.png) and their text-hidden passes (bg*.png)
        decoded/           key frames decoded back from banner.mp4 (what the checks look at)
        sheet.png          the decoded key frames side by side, for a human

The other 230-odd rendered frames are deleted after the encode: at ~2-3 MB
each they would cost ~600 MB per banner.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from banners import render
from banners.core import page as page_mod
from banners.core.kit import Font, Kit
from banners.core.rules import BannerRules
from banners.core.template import Template

OUT = Path(__file__).resolve().parents[1] / "out" / "banners"
SHEET_TILE = 270   # px per tile: a phone-feed thumbnail is about this size


@dataclass(frozen=True)
class Rendered:
    folder: Path
    mp4: Path
    layout: Dict[str, Any]
    key_frames: Dict[int, Path]     # frame -> decoded PNG
    background: Dict[int, Path]     # frame -> rendered PNG with text and buttons hidden
    page: page_mod.Page


def banner_dir(out: Path, kit: Kit, template: Template, placement: str, locale: str) -> Path:
    return out / kit.id / template.id / placement / locale


def render_banner(rules: BannerRules, template: Template, kit: Kit, fonts: Mapping[str, Font], placement: str,
                  locale: str, out: Path = OUT, overrides: Optional[page_mod.Overrides] = None,
                  folder: Optional[Path] = None) -> Rendered:
    p = rules.placement(placement)
    folder = folder or banner_dir(out, kit, template, placement, locale)
    if folder.exists():
        shutil.rmtree(str(folder))
    pg = page_mod.build(template, kit, fonts, p, locale, overrides)
    (folder / "page").mkdir(parents=True)
    index = folder / "page" / "index.html"
    with index.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(pg.html)
    files = dict(pg.files, **{"index.html": index})
    keys = page_mod.key_frames(template, p.fps, pg.frames)
    frames_dir = folder / "frames"
    record = render.frames(rules, files=files, page="index.html", width=p.width, height=p.height,
                           frame_list=list(range(pg.frames)), out_dir=frames_dir, hide_text_frames=keys,
                           locale=locale)
    mp4 = folder / "banner.mp4"
    render.encode(rules, rules.encode, frames_dir, p.fps, mp4, p.pix_fmt)
    keep = {"f%05d.png" % n for n in keys} | {"bg%05d.png" % n for n in keys}
    for f in frames_dir.glob("*.png"):
        if f.name not in keep:
            f.unlink()
    decoded = render.decode(rules, mp4, keys, folder / "decoded")
    render.contact_sheet(rules, [decoded[n] for n in keys], folder / "sheet.png", columns=len(keys),
                         tile_width=SHEET_TILE)
    layout = dict(record, page={"frames": pg.frames, "fps": pg.fps, "key_frames": keys, "colours": pg.colours,
                                "font_px": pg.font_px, "text_layers": list(pg.text_layers),
                                "locale": locale, "placement": placement})
    layout.pop("refused", None)
    with (folder / "layout.json").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(layout, sort_keys=True, separators=(",", ":")) + "\n")
    for f in (frames_dir / "job.json", frames_dir / "layout.json"):
        if f.exists():
            f.unlink()   # job.json holds absolute paths; the layout lives one level up
    return Rendered(folder, mp4, layout, decoded, {n: frames_dir / ("bg%05d.png" % n) for n in keys}, pg)
