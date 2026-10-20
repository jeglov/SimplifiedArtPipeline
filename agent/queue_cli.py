"""The human side of the approval queue.

    artpipe queue list [--status S] [--kind approval|handover]
    artpipe queue show ID
    artpipe queue approve ID [--note TEXT]     the agent may then run it (queue_run)
    artpipe queue reject ID [--note TEXT]
    artpipe queue resolve ID --note TEXT       close a hand-over

Approving is deliberately a human command: the MCP server has no tool for it.
Exit codes: 0 ok / 2 tool error (unknown id, wrong state).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence

from agent.core import queue


def _line(item) -> str:
    if item["kind"] == "approval":
        return "%-30s %-9s %-24s %s" % (item["id"], item["status"], item["action"], item["reason"][:60])
    return "%-30s %-9s %-24s %s" % (item["id"], item["status"], item["tool"], item["reason"].splitlines()[0][:60])


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="artpipe queue", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--status", default="")
    ls.add_argument("--kind", default="", choices=("", "approval", "handover"))
    sh = sub.add_parser("show")
    sh.add_argument("id")
    for name in ("approve", "reject"):
        d = sub.add_parser(name)
        d.add_argument("id")
        d.add_argument("--note", default="")
    rs = sub.add_parser("resolve")
    rs.add_argument("id")
    rs.add_argument("--note", required=True)
    args = p.parse_args(argv)
    try:
        if args.command == "list":
            items = queue.list_items(status=args.status, kind=args.kind)
            for item in items:
                print(_line(item))
            if not items:
                print("artpipe queue: empty")
            return 0
        if args.command == "show":
            print(json.dumps(queue.read(args.id), indent=2, sort_keys=True))
            return 0
        if args.command in ("approve", "reject"):
            item = queue.decide(args.id, args.command == "approve", args.note)
            print("artpipe queue: %s %s (%s): %s" % (item["status"], item["id"], item["action"],
                                                     " ".join(item["args"])))
            return 0
        item = queue.resolve(args.id, args.note)
        print("artpipe queue: resolved %s" % item["id"])
        return 0
    except (KeyError, ValueError) as exc:
        print("artpipe queue: %s" % (exc.args[0] if exc.args else exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
