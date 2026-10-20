"""Kit art: make a kit's images, each with its provenance sidecar.

    artpipe banners art hero KIT --from CONCEPT_RUN         cut-out of a Phase 3 concept (image + mask)
    artpipe banners art background KIT --prompt P [--seed N] [--repair [--template T]]
                                                             Z-Image on the pinned ComfyUI, once per game;
                                                             --repair: retry the seed while the hero would
                                                             blend in (kit.background, Phase 3 repair engine)
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
from typing import Any, Dict, Mapping, Optional, Tuple

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


def _background_pairs(prompt: str, seed: int, width: int, height: int):
    return ["prompt=" + " ".join(prompt.split()), "seed=%d" % seed, "width=%d" % width, "height=%d" % height]


def background(kit_dir: Path, prompt: str, seed: int, width: int, height: int,
               out_dir: Optional[Path] = None) -> Path:
    """One background per game on the pinned ComfyUI (the concept workflow; its mask is ignored)."""
    from gen import cli as gen_cli  # only this command needs ComfyUI
    code, message, run_dir = gen_cli.generate(BACKGROUND_WORKFLOW, _background_pairs(prompt, seed, width, height),
                                              out_dir or gen_cli.RUNS)
    if code != 0:
        raise ToolError("background generation failed: %s" % message)
    return keep_background(kit_dir, run_dir, seed, width, height)


def keep_background(kit_dir: Path, run_dir: Path, seed: int, width: int, height: int,
                    repair: Optional[Dict[str, Any]] = None) -> Path:
    """Copy a background run's image into the kit with its provenance sidecar."""
    m = _manifest(run_dir)
    image = run_dir / "image.png"
    record = {"kind": "generated", "licence": "generated", "models": _models(m),
              "from": {"run_id": m["run_id"], "run": _shown(run_dir), "workflow": BACKGROUND_WORKFLOW,
                       "prompt": m["params"].get("prompt"), "seed": seed, "width": width, "height": height,
                       "image_sha256": _sha(image)}}
    if repair:
        record["settings"] = {"repair": repair}
    return _write(kit_dir, "background", image.read_bytes(), record)


BACKGROUND_STAGE = "background"
BACKGROUND_REPORT = "background.report.json"


def background_report(run_dir: Path, kit_dir: Path, template_id: str, rules: BannerRules):
    """The kit.background check of a generated image against the kit's hero, as the stage's report.

    In the kit report kit.background is a warning (a human may accept a
    clash on purpose); here it is the gate of the generation stage, so it is
    an error and the repair loop retries on it.
    """
    import dataclasses
    from banners.core import template as template_mod
    from banners.core.checks import kit_background
    from banners.core.checks.context import KitContext
    from validate.core.result import Report, Severity
    kit = kit_mod.load(str(kit_dir))
    image = run_dir / "image.png"
    trial = dataclasses.replace(kit, images=dict(kit.images, background=kit_mod.Image(
        "background", image, "generated", kit_mod.provenance_path(image), None)))
    ctx = KitContext(template_mod.load(template_id), trial, kit_mod.load_fonts(), rules, ("en",),
                     tuple(sorted(rules.placements)))
    result = dataclasses.replace(kit_background.run(ctx), severity=Severity.ERROR)
    report = Report(_shown(image), _sha(image), "banner_background", results=[result], tool="artpipe.banners.art")
    path = run_dir / BACKGROUND_REPORT
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(report.to_json())
    return report, path


def background_repaired(kit_dir: Path, prompt: str, seed: int, width: int, height: int, rules: BannerRules,
                        template_id: str = "hero_endcard", out_dir: Optional[Path] = None
                        ) -> Tuple[int, str, Optional[Path]]:
    """Generate, check the hero against it, retry by seed (generation.yaml `repair`). Keeps the accepted one.

    Returns (exit code, message, the kit's background.png or None if handed over).
    """
    from gen import cli as gen_cli
    from gen.core import repair as gen_repair
    runs = out_dir or gen_cli.RUNS
    run_dirs: Dict[int, Path] = {}

    def attempt(s: int) -> "gen_repair.Outcome":
        try:
            code, message, run_dir = gen_cli.generate(BACKGROUND_WORKFLOW, _background_pairs(prompt, s, width, height),
                                                      runs, keep_server=False)
        except ToolError as exc:
            return gen_repair.Outcome(2, message=str(exc))
        if code != 0:
            return gen_repair.Outcome(2, message=message)
        run_dirs[s] = run_dir
        report, path = background_report(run_dir, kit_dir, template_id, rules)
        execution = (json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")).get("execution") or {})
        return gen_repair.Outcome(report.exit_code, report.to_dict(), _shown(path), _shown(run_dir),
                                  execution.get("seconds"), "(reused)" in message, message)

    request = {"kit": kit_mod.load(str(kit_dir)).id, "template": template_id, "prompt": " ".join(prompt.split()),
               "width": width, "height": height, "base_seed": seed}
    code, message, rec = gen_cli.with_repair(BACKGROUND_STAGE, request, seed, attempt, runs)
    if code != 0:
        return code, message, None
    last = rec["attempts"][-1]
    out = keep_background(kit_dir, run_dirs[last["seed"]], last["seed"], width, height,
                          {"repair_id": rec["repair_id"], "attempts": len(rec["attempts"])})
    return code, message, out


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
