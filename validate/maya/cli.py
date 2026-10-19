"""Command line entry point: validate one or more files, one JSON report each.

    mayapy -m validate.maya.cli FILE [FILE ...] --class prop_small [--out out]

Writes <out>/<file name>.report.json per input and prints one summary line
per file. Exit code is the worst across files: 0 pass, 1 asset failed,
2 tool error. Agents should read the report files, not stdout: Maya prints
its own startup noise to stdout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import List, Optional

from validate.core import conventions as conv
from validate.core.conventions import AssetClass, Conventions
from validate.core.result import EXIT_TOOL_ERROR, Report


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):  # 1 MB at a time
            h.update(chunk)
    return h.hexdigest()


def parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="validate.maya.cli", description=__doc__.splitlines()[0])
    p.add_argument("files", nargs="+", type=Path, help=".ma, .mb or .fbx files")
    p.add_argument("--class", dest="asset_class", required=True,
                   help="asset class from conventions/mesh_budgets.yaml, e.g. prop_small")
    p.add_argument("--out", type=Path, default=Path("out"), help="report folder (default: out)")
    p.add_argument("--conventions", type=Path, default=None, help="conventions folder (default: repo's)")
    return p.parse_args(argv)


def validate_file(path: Path, conventions: Conventions, asset_class: AssetClass) -> Report:
    """Open one file and run all checks. Never raises: failures become tool_error."""
    from validate.maya import context, registry, session

    if not path.is_file():
        return Report(str(path), "", asset_class.name, tool_error="file not found: %s" % path)

    digest = sha256(path)
    try:
        session.open_scene(path)
        ctx = context.build(str(path), conventions, asset_class)
    except Exception as exc:  # noqa: BLE001
        return Report(str(path), digest, asset_class.name,
                      tool_error="could not open file: %s: %s" % (type(exc).__name__, exc))

    results, errors = registry.run_all(ctx)
    return Report(str(path), digest, asset_class.name, results=results,
                  tool_error="; ".join(errors) or None)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    # Load conventions BEFORE starting Maya: a typo in --class should fail
    # in under a second, not after a 20 s Maya boot.
    try:
        conventions = conv.load(args.conventions)
        asset_class = conventions.asset_class(args.asset_class)
    except (conv.ConventionError, KeyError) as exc:
        # str(KeyError) adds quotes around the message; args[0] doesn't.
        print("artpipe: %s" % exc.args[0], file=sys.stderr)
        return EXIT_TOOL_ERROR

    from validate.maya import session
    session.start()
    try:
        args.out.mkdir(parents=True, exist_ok=True)
        worst = 0
        for path in args.files:
            report = validate_file(path, conventions, asset_class)
            # Full file name, not stem: SM_Crate.ma and SM_Crate.fbx must not
            # overwrite each other's report.
            out_file = args.out / (path.name + ".report.json")
            # newline="\n": identical bytes on Windows and Linux.
            with out_file.open("w", encoding="utf-8", newline="\n") as fh:
                fh.write(report.to_json())
            print("%-5s %s %s" % (report.status.value.upper(), out_file,
                                  json.dumps(report.summary(), sort_keys=True)))
            worst = max(worst, report.exit_code)
        return worst
    finally:
        session.stop()


if __name__ == "__main__":
    sys.exit(main())
