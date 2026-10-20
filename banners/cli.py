"""UA creatives from one template: install the renderer, make kit art, check kits.

    artpipe banners setup                    download + verify the pinned ffmpeg and Chromium, npm ci the renderer
    artpipe banners status                   JSON: every pinned tool, installed or not
    artpipe banners kit KIT --template T [--locale L ...] [--placement P ...]
                                             kit.* checks -> out/banners/kits/<kit>.<template>.kit.report.json
    artpipe banners render KIT --template T [--placement P] [--locale L]
                                             one banner -> out/banners/<kit>/<template>/<placement>/<locale>/
    artpipe banners art hero KIT --from CONCEPT_RUN
    artpipe banners art background KIT --prompt P [--seed N] [--width W --height H]
    artpipe banners art logo KIT

KIT is a kit id (banners/kits/<id>/) or folder, T a template id
(banners/templates/<id>.yaml) or file. Reports use conventions/report.schema.json.

Exit codes: 0 pass / 1 the kit or banner failed its checks / 2 tool error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, Sequence, Tuple

from banners import art, build, tools
from banners.core import ToolError
from banners.core import checks as banner_checks
from banners.core import kit as kit_mod
from banners.core import rules as banner_rules
from banners.core import template as template_mod
from banners.core.checks.context import KitContext
from validate.core.conventions import ConventionError
from validate.core.paths import report_path
from validate.core.result import Report

EXIT_OK, EXIT_TOOL_ERROR = 0, 2
OUT = tools.ROOT / "out" / "banners"
KIT_TOOL = "artpipe.banners.kit"
DEFAULT_LOCALES = ("en",)


def kit_context(template: str, kit: str, locales: Sequence[str], placements: Sequence[str]) -> KitContext:
    rules = banner_rules.load()
    for pid in placements:
        rules.placement(pid)  # unknown placement: ConventionError listing them
    return KitContext(template_mod.load(template), kit_mod.load(kit), kit_mod.load_fonts(), rules,
                      tuple(locales), tuple(placements))


def check_kit(ctx: KitContext, out_dir: Path = OUT / "kits") -> Tuple[Report, Path]:
    results, errors = banner_checks.run_all(banner_checks.KIT_CHECKS, ctx)
    source = ctx.kit.path / "kit.yaml"
    report = Report(report_path(source), tools.sha256_file(source), "banner_kit", results=results,
                    tool_error="; ".join(errors) or None, tool=KIT_TOOL)
    out = out_dir / ("%s.%s.kit.report.json" % (ctx.kit.id, ctx.template.id))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(report.to_json())
    return report, out


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="artpipe banners", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="install the pinned renderer tools")
    sub.add_parser("status", help="JSON status of the pinned tools")
    k = sub.add_parser("kit", help="check a game kit against a template")
    k.add_argument("kit")
    k.add_argument("--template", required=True)
    k.add_argument("--locale", action="append", default=None, help="repeatable (default: en)")
    k.add_argument("--placement", action="append", default=None, help="repeatable (default: all)")
    k.add_argument("--out", default=str(OUT / "kits"))
    rd = sub.add_parser("render", help="render one banner (MP4, layout record, contact sheet)")
    rd.add_argument("kit")
    rd.add_argument("--template", required=True)
    rd.add_argument("--placement", default="ua_video_9x16")
    rd.add_argument("--locale", default="en")
    rd.add_argument("--out", default=str(OUT))
    a = sub.add_parser("art", help="make a kit image with its provenance sidecar")
    a_sub = a.add_subparsers(dest="slot", required=True)
    h = a_sub.add_parser("hero", help="cut-out of a passing Phase 3 concept run")
    h.add_argument("kit")
    h.add_argument("--from", dest="concept_run", required=True)
    b = a_sub.add_parser("background", help="Z-Image background on the pinned ComfyUI")
    b.add_argument("kit")
    b.add_argument("--prompt", required=True)
    b.add_argument("--seed", type=int, default=0)
    b.add_argument("--width", type=int, default=864)
    b.add_argument("--height", type=int, default=1536)
    lg = a_sub.add_parser("logo", help="KIT/logo.html rendered by the pinned Chromium")
    lg.add_argument("kit")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        rules = banner_rules.load()
        if args.command == "setup":
            for line in tools.setup(rules):
                print("artpipe banners: %s" % line)
            return EXIT_OK
        if args.command == "status":
            state = tools.status(rules)
            print(json.dumps(state, indent=2, sort_keys=True))
            return EXIT_OK if all(v["ok"] for v in state.values()) else EXIT_TOOL_ERROR
        if args.command == "kit":
            ctx = kit_context(args.template, args.kit, args.locale or DEFAULT_LOCALES,
                              args.placement or sorted(rules.placements))
            report, out = check_kit(ctx, Path(os.path.abspath(args.out)))
            stream = sys.stdout if report.exit_code == EXIT_OK else sys.stderr
            print("artpipe banners: %s %s" % (report.status.value.upper(), report_path(out)), file=stream)
            return report.exit_code
        if args.command == "render":
            ctx = kit_context(args.template, args.kit, [args.locale], [args.placement])
            report, kit_report = check_kit(ctx)
            if report.exit_code != EXIT_OK:
                print("artpipe banners: the kit fails its checks, not rendering: %s" % report_path(kit_report), file=sys.stderr)
                return report.exit_code
            r = build.render_banner(rules, ctx.template, ctx.kit, ctx.fonts, args.placement, args.locale,
                                    Path(os.path.abspath(args.out)))
            print("artpipe banners: OK %s (sheet: %s)" % (report_path(r.mp4), report_path(r.folder / "sheet.png")))
            return EXIT_OK
        kit_dir = kit_mod.load(args.kit).path
        if args.slot == "hero":
            out = art.hero(kit_dir, Path(os.path.abspath(args.concept_run)))
        elif args.slot == "background":
            out = art.background(kit_dir, args.prompt, args.seed, args.width, args.height)
        else:
            out = art.logo(kit_dir, rules)
        print("artpipe banners: OK %s (+ %s)" % (report_path(out), kit_mod.provenance_path(out).name))
        return EXIT_OK
    except (ToolError, ConventionError, ValueError, OSError) as exc:
        print("artpipe banners: %s" % str(exc).strip("'\""), file=sys.stderr)
        return EXIT_TOOL_ERROR


if __name__ == "__main__":
    sys.exit(main())
