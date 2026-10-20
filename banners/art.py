"""Kit art: make a kit's images, each with its provenance sidecar.

    artpipe banners art hero KIT --from CONCEPT_RUN         cut-out of a Phase 3 concept (image + mask)
    artpipe banners art background KIT --prompt P [--seed N] Z-Image on the pinned ComfyUI, once per game
    artpipe banners art logo KIT                             KIT/logo.html rendered by the pinned Chromium

Each writes <kit>/<slot>.png and <slot>.png.provenance.json: the output's
sha256, how it was made, from what (run ids, input hashes, prompt, seed),
and the models with their licences. kit.yaml only names the file and its
licence; `kit.files` and `kit.licences` check the sidecar.

AI makes pictures only: never text or a logo. A diffusion model garbles
letters and gives no certainty about trademarks, so the logo is a page set
in a licensed font.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from banners import render
from banners.core import ToolError
from banners.core import cutout as cut
from banners.core import kit as kit_mod
from banners.core.rules import BannerRules
from validate.core.image import decode_png, encode_png
from validate.core.paths import report_path

SCHEMA_VERSION = "1.0"
TOOL = "artpipe.banners.art"
BACKGROUND_WORKFLOW = "concept_zimage"
LOGO_SIZE = (1400, 440)
HERO_RAMP = (16, 240)
HERO_MARGIN = 0.03


def _sha(path: Path) -> str:
    from banners.tools import sha256_file
    return sha256_file(path)


def _write(kit_dir: Path, slot: str, png: bytes, record: Dict[str, Any]) -> Path:
    out = kit_dir / ("%s.png" % slot)
    out.write_bytes(png)
    record = dict(record, schema_version=SCHEMA_VERSION, tool=TOOL, slot=slot, output=out.name,
                  output_sha256=_sha(out))
    with kit_mod.provenance_path(out).open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return out


def _manifest(run_dir: Path) -> Dict[str, Any]:
    try:
        m = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ToolError("%s has no readable manifest.json" % run_dir)
    if m.get("status") != "ok":
        raise ToolError("run %s did not succeed" % m.get("run_id"))
    return m


def _shown(path: Path) -> str:
    """Repo-relative, or only the name for a file outside the repo: no machine paths in records (#46)."""
    shown = report_path(path)
    return shown if not Path(shown).is_absolute() else "<outside-repo>/" + path.name


def _models(manifest: Mapping[str, Any]):
    return [{"id": m["id"], "sha256": m["sha256"], "licence": m["licence"]} for m in manifest.get("models") or []]


def hero(kit_dir: Path, concept_run: Path) -> Path:
    """The hero: a passing Phase 3 concept, cut out with its own BiRefNet mask."""
    m = _manifest(concept_run)
    try:
        status = json.loads((concept_run / "concept.report.json").read_text(encoding="utf-8"))["status"]
    except (OSError, ValueError, KeyError):
        raise ToolError("%s has no concept.report.json: not a concept run" % concept_run)
    if status != "pass":
        raise ToolError("the concept in %s failed its checks: cut out a passing one" % concept_run)
    image, mask = concept_run / "image.png", concept_run / "mask.png"
    w, h, data = cut.cutout(decode_png(image), decode_png(mask), *HERO_RAMP, margin=HERO_MARGIN)
    record = {"kind": "cutout", "licence": "generated", "models": _models(m),
              "from": {"run_id": m["run_id"], "run": _shown(concept_run), "prompt": m["params"].get("prompt"),
                       "seed": m["params"].get("seed"), "image_sha256": _sha(image), "mask_sha256": _sha(mask)},
              "settings": {"alpha_ramp": list(HERO_RAMP), "margin": HERO_MARGIN}, "size": [w, h]}
    return _write(kit_dir, "hero", encode_png(w, h, 4, data), record)


def background(kit_dir: Path, prompt: str, seed: int, width: int, height: int,
               out_dir: Optional[Path] = None) -> Path:
    """One background per game on the pinned ComfyUI (the concept workflow; its mask is ignored)."""
    from gen import cli as gen_cli  # only this command needs ComfyUI
    pairs = ["prompt=" + " ".join(prompt.split()), "seed=%d" % seed, "width=%d" % width, "height=%d" % height]
    code, message, run_dir = gen_cli.generate(BACKGROUND_WORKFLOW, pairs, out_dir or gen_cli.RUNS)
    if code != 0:
        raise ToolError("background generation failed: %s" % message)
    m = _manifest(run_dir)
    image = run_dir / "image.png"
    record = {"kind": "generated", "licence": "generated", "models": _models(m),
              "from": {"run_id": m["run_id"], "run": _shown(run_dir), "workflow": BACKGROUND_WORKFLOW,
                       "prompt": m["params"].get("prompt"), "seed": seed, "width": width, "height": height,
                       "image_sha256": _sha(image)}}
    return _write(kit_dir, "background", image.read_bytes(), record)


def logo(kit_dir: Path, rules: BannerRules, fonts: Optional[Mapping[str, kit_mod.Font]] = None) -> Path:
    """KIT/logo.html, rendered transparent by the pinned Chromium with the kit's display font, cropped."""
    kit = kit_mod.load(str(kit_dir))
    fonts = fonts or kit_mod.load_fonts()
    source = kit.path / "logo.html"
    fid = kit.fonts.get("display")
    if not source.is_file() or fid not in fonts:
        raise ToolError("logo needs %s and a display font in kit.yaml" % source)
    font = fonts[fid]
    with tempfile.TemporaryDirectory() as tmp:
        layout = render.frames(rules, files={"logo.html": source, "fonts/display.ttf": font.path}, page="logo.html",
                               width=LOGO_SIZE[0], height=LOGO_SIZE[1], frame_list=[0], out_dir=Path(tmp),
                               transparent=True)
        bad = [f for used in layout["fonts"].values() for f in used if not f["custom"]]
        if bad:
            raise ToolError("the logo fell back to system fonts: %s" % bad)
        w, h, data = cut.crop_to_alpha(decode_png(Path(tmp) / "f00000.png"), margin_px=6)
    record = {"kind": "rendered", "licence": "own-work", "models": [],
              "from": {"source": _shown(source), "source_sha256": _sha(source),
                       "fonts": [{"id": font.id, "sha256": font.sha256, "licence": font.licence}],
                       "chromium": layout["browser"]}, "size": [w, h]}
    return _write(kit.path, "logo", encode_png(w, h, 4, data), record)
