"""One template, many games: every kit x placement x locale, reused when nothing changed.

    artpipe banners batch --template T --games G1,G2,... [--placement P ...] [--locale L ...] [--no-repair]

For each game: the kit checks (a failing kit skips its banners: nothing is
rendered from a kit that can't make a complete banner). For each banner:
the input hash (banners/core/batch.py); if <banner>/inputs.json holds the
same hash and the report still matches banner.mp4, it is reused, otherwise
rendered through the repair loop. Then the batch-level check
`batch.distinct` (games whose end cards look alike), the batch contact sheet
(one row per banner: first frame, mid hook, end card) and the summary:

    out/banners/batch/<template>.batch.json      conventions/banner_batch.schema.json
    out/banners/batch/<template>.sheet.png

Exit codes: 0 every banner passed / 1 a banner or kit failed or was handed over / 2 tool error.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from banners import build, check as banner_check, render, repair as banner_repair, tools
from banners.core import ToolError
from banners.core import batch as bc
from banners.core import kit as kit_mod
from banners.core import template as template_mod
from banners.core.checks.banner_context import sample
from banners.core.checks.context import KitContext
from banners.core.rules import BannerRules
from validate.core.conventions import ConventionError
from validate.core.image import decode_png
from validate.core.paths import report_path

INPUTS = "inputs.json"
PACKAGE = Path(__file__).resolve().parent


def code_hash() -> str:
    """sha256 over the banner code: every .py of the banners package, the renderer script and its lockfile."""
    files = sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)
    files += [PACKAGE / "render" / "render.mjs", PACKAGE / "render" / "package-lock.json"]
    return bc.input_hash({p.relative_to(PACKAGE).as_posix(): tools.sha256_file(p) for p in files})


def kit_inputs(kit: kit_mod.Kit, fonts: Mapping[str, kit_mod.Font]) -> Dict[str, Any]:
    return {"id": kit.id, "kit_yaml": tools.sha256_file(kit.path / "kit.yaml"),
            "images": {slot: tools.sha256_file(img.path) for slot, img in sorted(kit.images.items())},
            "fonts": {fid: fonts[fid].sha256 for fid in sorted(set(kit.fonts.values())) if fid in fonts}}


def _read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def reusable(folder: Path, digest: str) -> Optional[Dict[str, Any]]:
    """The earlier report if this banner was made from exactly these inputs and its files are intact."""
    prev, report = _read(folder / INPUTS), _read(folder / banner_check.REPORT)
    mp4 = folder / "banner.mp4"
    if not prev or prev.get("hash") != digest or not report or not mp4.is_file():
        return None
    if report["source"]["sha256"] != tools.sha256_file(mp4):
        return None
    return report


def _status(report: Mapping[str, Any], code: Optional[int] = None) -> str:
    if code is None:
        code = {"pass": 0, "fail": 1, "error": 2}[report["status"]]
    return {0: "pass", 1: "handed_over", 2: "error"}[code]


def one_banner(rules: BannerRules, template: template_mod.Template, kit: kit_mod.Kit, fonts, placement: str,
               locale: str, out: Path, repair: bool, parts: Mapping[str, Any]) -> Dict[str, Any]:
    folder = build.banner_dir(out, kit, template, placement, locale)
    digest = bc.input_hash(parts)
    record = {"kit": kit.id, "placement": placement, "locale": locale, "folder": report_path(folder),
              "hash": digest}
    report = reusable(folder, digest)
    if report is not None:
        rep = _read(folder / banner_repair.RECORD)
        record.update(reused=True, seconds=0.0, renders=(rep or {}).get("summary", {}).get("renders", 1))
    else:
        started = time.time()
        try:
            if repair:
                code, rec, folder = banner_repair.run(rules, template, kit, fonts, placement, locale, out)
                renders = rec["summary"]["renders"]
            else:
                r = build.render_banner(rules, template, kit, fonts, placement, locale, out)
                banner_check.check(rules, r.folder, template)
                code, renders = None, 1
        except ToolError as exc:
            record.update(status="error", reused=False, seconds=round(time.time() - started, 1), renders=0,
                          failed=[], warnings=[], message=str(exc))
            return record
        report = _read(folder / banner_check.REPORT)
        with (folder / INPUTS).open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"hash": digest, "inputs": parts}, indent=1, sort_keys=True) + "\n")
        record.update(reused=False, seconds=round(time.time() - started, 1), renders=renders)
        if code is not None:
            record["status"] = _status(report, code)
    record.setdefault("status", _status(report))
    record["failed"] = [r["id"] for r in report["results"] if not r["passed"] and r["severity"] == "error"]
    record["warnings"] = [r["id"] for r in report["results"] if not r["passed"] and r["severity"] == "warning"]
    record["report"] = report_path(folder / banner_check.REPORT)
    record["mp4_sha256"] = report["source"]["sha256"]
    return record


def _end_card_hist(folder: Path) -> Optional[List[float]]:
    layout = _read(folder / "layout.json")
    if not layout:
        return None
    keys = layout["page"]["key_frames"]
    last = folder / "decoded" / ("d%05d.png" % (len(keys) - 1))
    if not last.is_file():
        return None
    px = decode_png(last)
    return bc.histogram(sample(px, (0, 0, px.width, px.height), 8))


def distinct(rules: BannerRules, banners: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """batch.distinct: per placement x locale, game pairs whose end cards are alike (a warning)."""
    minimum = rules.checks.batch_distinct_min
    groups: Dict[Tuple[str, str], Dict[str, List[float]]] = {}
    for b in banners:
        if b["status"] in ("skipped", "error"):
            continue
        h = _end_card_hist(tools.ROOT / b["folder"])
        if h is not None:
            groups.setdefault((b["placement"], b["locale"]), {})[b["kit"]] = h
    pairs = []
    for (placement, locale), hists in sorted(groups.items()):
        for p in bc.distinct_pairs(hists, minimum):
            pairs.append(dict(p, placement=placement, locale=locale))
    compared = sum(len(h) * (len(h) - 1) // 2 for h in groups.values())
    return {"id": "batch.distinct", "severity": "warning", "passed": not pairs, "min_distance": minimum,
            "pairs_compared": compared, "pairs": pairs,
            "message": ("%d game pair(s) with end cards that look alike" % len(pairs)) if pairs else
            ("%d game pair(s) compared, all distinct" % compared if compared else "fewer than 2 games: nothing to compare")}


def batch_sheet(rules: BannerRules, banners: Sequence[Mapping[str, Any]], out: Path) -> Optional[Path]:
    """One row per rendered banner: first frame, the middle of the hook, the end card."""
    tiles = []
    for b in banners:
        if b["status"] in ("skipped", "error"):
            continue
        folder = tools.ROOT / b["folder"]
        layout = _read(folder / "layout.json")
        if not layout:
            continue
        keys = layout["page"]["key_frames"]
        picks = [0, len(keys) // 3, len(keys) - 1]
        tiles += [folder / "decoded" / ("d%05d.png" % i) for i in picks]
    if not tiles or not all(t.is_file() for t in tiles):
        return None
    render.contact_sheet(rules, tiles, out, columns=3, tile_width=build.SHEET_TILE)
    return out


def run(rules: BannerRules, template_id: str, kit_ids: Sequence[str], placements: Sequence[str],
        locales: Sequence[str], out: Path = build.OUT, repair: bool = True) -> Tuple[int, Dict[str, Any], Path]:
    started = time.time()
    template = template_mod.load(template_id)
    fonts = kit_mod.load_fonts()
    for pid in placements:
        rules.placement(pid)
    from banners.cli import check_kit   # the kit report writer (cli imports this module lazily)
    common = {"template": {"id": template.id, "sha256": tools.sha256_file(template.path)}, "code": code_hash()}
    kits, banners = [], []
    for kid in kit_ids:
        try:
            kit = kit_mod.load(kid)
        except ConventionError as exc:
            kits.append({"kit": kid, "status": "error", "report": None, "message": str(exc)})
            banners += [{"kit": kid, "placement": p, "locale": l, "status": "skipped", "reused": False, "failed": [],
                         "warnings": [], "renders": 0, "seconds": 0.0, "message": "kit unreadable"}
                        for p in placements for l in locales]
            continue
        report, path = check_kit(KitContext(template, kit, fonts, rules, tuple(locales), tuple(placements)),
                                 out / "kits")
        kits.append({"kit": kit.id, "status": report.status.value, "report": report_path(path),
                     "failed": [r.id for r in report.results if r.blocking]})
        if report.exit_code != 0:
            banners += [{"kit": kit.id, "placement": p, "locale": l, "status": "skipped", "reused": False,
                         "failed": [], "warnings": [], "renders": 0, "seconds": 0.0,
                         "message": "kit fails %s" % (", ".join(kits[-1]["failed"]) or "its checks")}
                        for p in placements for l in locales]
            continue
        for p in placements:
            for l in locales:
                parts = dict(common, kit=kit_inputs(kit, fonts), rules=bc.rules_record(rules, p), locale=l,
                             repair=repair)
                banners.append(one_banner(rules, template, kit, fonts, p, l, out, repair, parts))
    dist = distinct(rules, banners)
    batch_dir = out / "batch"
    batch_dir.mkdir(parents=True, exist_ok=True)
    sheet = batch_sheet(rules, banners, batch_dir / ("%s.sheet.png" % template.id))
    record = {"schema_version": bc.SCHEMA_VERSION, "tool": bc.TOOL, "template": template.id,
              "games": list(kit_ids), "placements": list(placements), "locales": list(locales), "repair": repair,
              "exit_code": bc.exit_code(banners, kits), "kits": kits, "banners": banners, "distinct": dist,
              "sheet": report_path(sheet) if sheet else None,
              "summary": bc.summary(banners, kits, dist, time.time() - started)}
    path = batch_dir / ("%s.batch.json" % template.id)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return record["exit_code"], record, path
