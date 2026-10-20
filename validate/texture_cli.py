"""Check textures against conventions/texture_rules.yaml before Unity sees them.

    artpipe texture FILE [FILE ...] [--class prop_small] [--out out]

Writes <out>/<file name>.report.json per texture (contract:
conventions/report.schema.json, tool "artpipe.validate.texture") with the
same check ids as the Unity import (texture.name, texture.power_of_two...).
Without --class the shared default max size applies.

Exit codes: 0 pass / 1 a texture failed / 2 tool error (unreadable file).
Pure Python: no Maya, no Pillow.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import List, Optional

from validate.core import conventions as conv
from validate.core.image import ImageError
from validate.core.paths import report_path
from validate.core.result import EXIT_PASS, Report
from validate.core.texture import source_checks

TOOL = "artpipe.validate.texture"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_file(path: Path, c: conv.Conventions, asset_class: Optional[str]) -> Report:
    shown = report_path(path)
    cls = asset_class or "default"
    if not path.is_file():
        return Report(shown, "", cls, tool_error="file not found: %s" % shown, tool=TOOL)
    digest = sha256(path)
    try:
        results = source_checks(path, c, asset_class, shown_as=shown)
    except ImageError as exc:
        return Report(shown, digest, cls, tool_error=str(exc), tool=TOOL)
    return Report(shown, digest, cls, results=results, tool=TOOL)


def parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="artpipe texture", description=__doc__.splitlines()[0])
    p.add_argument("files", nargs="+", type=Path)
    p.add_argument("--class", dest="asset_class", default=None, help="asset class (sets the max size)")
    p.add_argument("--out", type=Path, default=Path("out"), help="report folder (default: out)")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        c = conv.load()
        if args.asset_class is not None:
            c.asset_class(args.asset_class)  # unknown class = tool error, listed options
    except (conv.ConventionError, KeyError) as exc:
        print("artpipe texture: %s" % exc, file=sys.stderr)
        return 2
    worst = EXIT_PASS
    args.out.mkdir(parents=True, exist_ok=True)
    for f in args.files:
        report = check_file(f, c, args.asset_class)
        out = args.out / (f.name + ".report.json")
        out.write_text(report.to_json(), encoding="utf-8")
        worst = max(worst, report.exit_code)
        print("%s %s -> %s" % (report.status.value.upper(), report_path(f), report_path(out)))
    return worst


if __name__ == "__main__":
    sys.exit(main())
