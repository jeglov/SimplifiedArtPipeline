"""UA creatives from one template: install the renderer, render, check, repair.

    artpipe banners setup      download + verify the pinned ffmpeg and Chromium, npm ci the renderer
    artpipe banners status     JSON: every pinned tool, installed or not

Exit codes: 0 pass / 1 a banner failed its checks / 2 tool error.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence

from banners import tools
from banners.core import ToolError
from banners.core import rules as banner_rules
from validate.core.conventions import ConventionError

EXIT_OK, EXIT_TOOL_ERROR = 0, 2


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="artpipe banners", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="install the pinned renderer tools")
    sub.add_parser("status", help="JSON status of the pinned tools")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        rules = banner_rules.load()
        if args.command == "setup":
            for line in tools.setup(rules):
                print("artpipe banners: %s" % line)
            return EXIT_OK
        state = tools.status(rules)
        print(json.dumps(state, indent=2, sort_keys=True))
        return EXIT_OK if all(v["ok"] for v in state.values()) else EXIT_TOOL_ERROR
    except (ToolError, ConventionError) as exc:
        print("artpipe banners: %s" % str(exc).strip("'\""), file=sys.stderr)
        return EXIT_TOOL_ERROR


if __name__ == "__main__":
    sys.exit(main())
