"""Check a rendered banner: banner.* checks -> <banner folder>/banner.report.json.

The context is rebuilt from the folder alone (layout.json, banner.mp4,
decoded/, frames/), so a banner can be re-checked without rendering it
again, e.g. after a rule changes in conventions/banners.yaml.

Report: conventions/report.schema.json, tool artpipe.banners.banner,
asset_class = the placement id, source = banner.mp4 with its sha256.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Sequence, Tuple

from banners import render, tools
from banners.core import ToolError
from banners.core import checks as banner_checks
from banners.core import template as template_mod
from banners.core.checks.banner_context import BannerContext
from banners.core.rules import BannerRules
from validate.core.paths import report_path
from validate.core.result import Report

TOOL = "artpipe.banners.banner"
REPORT = "banner.report.json"


def context(rules: BannerRules, folder: Path, template: Optional[template_mod.Template] = None) -> BannerContext:
    try:
        layout = json.loads((folder / "layout.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError("%s has no readable layout.json: render it first (%s)" % (folder, exc))
    page = layout["page"]
    template = template or template_mod.load(str(tools.ROOT / page["template"]))
    keys = page["key_frames"]
    decoded = {n: folder / "decoded" / ("d%05d.png" % i) for i, n in enumerate(keys)}
    background = {n: folder / "frames" / ("bg%05d.png" % n) for n in keys}
    missing = [p.name for p in list(decoded.values()) + list(background.values()) if not p.is_file()]
    if missing:
        raise ToolError("%s is incomplete (missing %s): render it again" % (folder, ", ".join(missing[:3])))
    facts = render.probe(rules, folder / "banner.mp4")
    return BannerContext(template, rules.placement(page["placement"]), rules, layout, facts, decoded, background)


def check(rules: BannerRules, folder: Path, template: Optional[template_mod.Template] = None,
          only: Optional[Sequence[str]] = None) -> Tuple[Report, Path]:
    """All banner checks, or only the ids in `only` (a PARTIAL report, for an agent asking one question)."""
    mp4 = folder / "banner.mp4"
    shown = report_path(mp4)
    selected = [c for c in banner_checks.BANNER_CHECKS if only is None or c.ID in only]
    unknown = sorted(set(only or ()) - {c.ID for c in banner_checks.BANNER_CHECKS})
    if unknown:
        raise ToolError("unknown banner check id(s) %s" % ", ".join(unknown))
    try:
        ctx = context(rules, folder, template)
        results, errors = banner_checks.run_all(selected, ctx)
        report = Report(shown, tools.sha256_file(mp4), ctx.placement.id, results=results,
                        tool_error="; ".join(errors) or None, tool=TOOL)
    except ToolError as exc:
        report = Report(shown, tools.sha256_file(mp4) if mp4.is_file() else "", "banner", tool_error=str(exc),
                        tool=TOOL)
    out = folder / REPORT
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(report.to_json())
    return report, out
