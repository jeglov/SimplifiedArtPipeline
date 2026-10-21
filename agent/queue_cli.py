"""The human side of the approval queue.

    artpipe queue list [--status S] [--kind approval|handover]
    artpipe queue show ID
    artpipe queue approve ID [--note TEXT]     the agent may then run it (queue_run)
    artpipe queue reject ID [--note TEXT]
    artpipe queue resolve ID --note TEXT       close a hand-over

Approving is deliberately a human command: the MCP server has no tool for it.
`approve` and `resolve` also need a person at a real console, who types the
item id back. An agent's shell can run this CLI too, and its stdin is a pipe or
NUL, never a console (probed: Python's isatty() is True for NUL on Windows, so
the check asks Windows with GetConsoleMode instead). That stops an agent
approving its own request by accident or by habit; it is not a signature
(a scripted console can still type, backlog #65, #38).
Exit codes: 0 ok / 2 tool error (unknown id, wrong state, no console, not confirmed).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Callable, Optional, Sequence

from agent.core import queue


def _line(item) -> str:
    if item["kind"] == "approval":
        return "%-30s %-9s %-24s %s" % (item["id"], item["status"], item["action"], item["reason"][:60])
    return "%-30s %-9s %-24s %s" % (item["id"], item["status"], item["tool"], item["reason"].splitlines()[0][:60])


def at_console() -> bool:
    """True only when stdin is an interactive console a person can type into."""
    if os.name != "nt":
        return sys.stdin.isatty()
    import ctypes
    import msvcrt
    from ctypes import wintypes
    try:
        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
    except (AttributeError, OSError, ValueError):
        return False
    mode = wintypes.DWORD()
    return bool(ctypes.windll.kernel32.GetConsoleMode(wintypes.HANDLE(handle), ctypes.byref(mode)))


def _confirm(item, verb: str, console: Callable[[], bool], ask: Callable[[str], str]) -> None:
    """A person reads the item and types its id back, or nothing changes (ValueError)."""
    if not console():
        raise ValueError("%s needs a person at an interactive console: run `artpipe queue %s %s` yourself "
                         "in a terminal (an agent's shell or the MCP server can't)" % (verb, verb, item["id"]))
    print(_line(item))
    if item["kind"] == "approval":
        print("  runs: artpipe %s" % " ".join(item["args"]))
    try:
        typed = ask("Type the id to %s it: " % verb).strip()
    except EOFError:
        typed = ""
    if typed != item["id"]:
        raise ValueError("not confirmed: %s is unchanged" % item["id"])


def main(argv: Optional[Sequence[str]] = None, console: Callable[[], bool] = at_console,
         ask: Callable[[str], str] = input) -> int:
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
        if args.command in ("approve", "resolve"):
            _confirm(queue.read(args.id), args.command, console, ask)
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
