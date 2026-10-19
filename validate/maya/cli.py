"""Command line entry point: validate one or more files, one JSON report each.

    mayapy -m validate.maya.cli FILE [FILE ...] --class prop_small [--out out] [--max-offenders N]
                                [--fix [--fixed-dir out/fixed]] [--export DIR]
    (or, from the repo root: artpipe validate FILE ... --class prop_small)

Writes <out>/<file name>.report.json per input and prints one summary line
per file. Exit code is the worst across files: 0 pass, 1 asset failed,
2 tool error. With --fix, the report (and exit code) describe the FIXED
file. Agents should read the report files, not stdout: Maya prints its own
startup noise to stdout.

With --export DIR, every file that PASSES is exported to DIR/<stem>.fbx as
canonical ASCII FBX (conventions/fbx_export.yaml), re-opened and validated
again, and that FBX's own report is written next to it as
DIR/<stem>.fbx.report.json. Its source.sha256 is the FBX's hash and
exported_from links back to the validated scene: this pair is what the
Unity import gate trusts. Failed assets are never exported.
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
from validate.core.paths import report_path
from validate.core.result import EXIT_TOOL_ERROR, OFFENDER_CAP, Report


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
    p.add_argument("--max-offenders", type=int, default=OFFENDER_CAP, metavar="N",
                   help="offenders written per check (default %d, 0 = all)" % OFFENDER_CAP)
    p.add_argument("--fix", action="store_true",
                   help="apply safe auto-fixes, save to --fixed-dir, re-validate the saved file")
    p.add_argument("--fixed-dir", type=Path, default=Path("out") / "fixed",
                   help="where fixed files are saved (default: out/fixed)")
    p.add_argument("--export", type=Path, default=None, metavar="DIR",
                   help="export each passing file to DIR/<stem>.fbx + DIR/<stem>.fbx.report.json")
    args = p.parse_args(argv)
    if args.max_offenders < 0:
        p.error("--max-offenders must be >= 0")
    return args


def validate_file(path: Path, conventions: Conventions, asset_class: AssetClass,
                  max_offenders: int = OFFENDER_CAP, fix_dir: Optional[Path] = None) -> Report:
    """Open one file and run all checks. Never raises: failures become tool_error.

    With fix_dir: apply the safe fixes, save the result to fix_dir/<file name>
    (the source is never overwritten), RE-OPEN the saved file and validate it
    from scratch. The report then describes the fixed file, lists what each
    fix did, and points back to the original in fixed_from.
    """
    from validate.maya import context, registry, session

    # Repo-relative, forward slashes: same report on every machine (backlog #3).
    shown = report_path(path)

    def report(source, digest, **kw):
        return Report(source, digest, asset_class.name, max_offenders=max_offenders, **kw)

    if not path.is_file():
        return report(shown, "", tool_error="file not found: %s" % shown)

    digest = sha256(path)
    try:
        session.open_scene(path)
        ctx = context.build(shown, conventions, asset_class)
    except Exception as exc:  # noqa: BLE001
        return report(shown, digest, tool_error="could not open file: %s: %s" % (type(exc).__name__, exc))

    results, errors = registry.run_all(ctx)
    if fix_dir is None or errors:
        return report(shown, digest, results=results, tool_error="; ".join(errors) or None)

    # --- fix mode -------------------------------------------------------------
    fixes, errors = registry.apply_fixes(lambda: context.build(shown, conventions, asset_class))
    if errors:  # a fix crashed: scene state unknown, save nothing
        return report(shown, digest, results=results, fixes=fixes, tool_error="; ".join(errors))
    if not any(f.applied for f in fixes):  # nothing changed: report the original as-is
        return report(shown, digest, results=results, fixes=fixes)

    # Always .ma: an FBX can't be re-saved as FBX before export (backlog #27),
    # and .mb would get a new hash on every save (backlog #28).
    fixed = fix_dir / (path.stem + ".ma")
    if fixed.resolve() == path.resolve():
        return report(shown, digest, results=results, fixes=fixes,
                      tool_error="refusing to overwrite the source file: use another --fixed-dir")
    fixed_shown = report_path(fixed)
    try:
        session.save_scene(fixed)
        # Validate what was SAVED, not the scene in memory: proves the file on
        # disk is fixed, and gives the report the new file's hash.
        session.open_scene(fixed)
        ctx = context.build(fixed_shown, conventions, asset_class)
    except Exception as exc:  # noqa: BLE001
        return report(shown, digest, results=results, fixes=fixes,
                      tool_error="could not save/re-open fixed file: %s: %s" % (type(exc).__name__, exc))
    results, errors = registry.run_all(ctx)
    return report(fixed_shown, sha256(fixed), results=results, fixes=fixes,
                  fixed_from={"path": shown, "sha256": digest}, tool_error="; ".join(errors) or None)


def validated_file(path: Path, report: Report, fix_dir: Optional[Path]) -> Path:
    """The file a report's verdict is about: the fixed copy if fixes applied."""
    return fix_dir / (path.stem + ".ma") if report.fixed_from is not None and fix_dir else path


def export_file(path: Path, validated_report: Report, conventions: Conventions,
                asset_class: AssetClass, export_dir: Path,
                max_offenders: int = OFFENDER_CAP) -> Report:
    """Export a validated scene to FBX and validate the FBX itself.

    `path` must be the file `validated_report` describes, and the report must
    pass. Never raises: failures become tool_error on the FBX's report.
    """
    from validate.maya import context, export, registry, session

    fbx = export_dir / (path.stem + ".fbx")
    fbx_shown = report_path(fbx)
    origin = {"path": validated_report.source_path, "sha256": validated_report.source_sha256}

    def report(digest, **kw):
        return Report(fbx_shown, digest, asset_class.name, max_offenders=max_offenders,
                      exported_from=origin, **kw)

    if validated_report.exit_code != 0:
        return report("", tool_error="refusing to export: the source did not pass validation")
    if fbx.resolve() == path.resolve():
        return report("", tool_error="refusing to overwrite the source file: use another --export folder")
    try:
        session.open_scene(path)
        info = export.export_fbx(fbx, conventions)
        # Validate what was WRITTEN: proves the FBX still passes after the
        # exporter's own changes (triangulation, unit and axis conversion).
        session.open_scene(fbx)
        ctx = context.build(fbx_shown, conventions, asset_class)
    except Exception as exc:  # noqa: BLE001
        return report(sha256(fbx) if fbx.is_file() else "",
                      tool_error="FBX export failed: %s: %s" % (type(exc).__name__, exc))
    results, errors = registry.run_all(ctx)
    rules = conventions.fbx_export
    return report(sha256(fbx), results=results, tool_error="; ".join(errors) or None, export={
        "format": "fbx-ascii",
        "settings_version": rules.settings_version,
        "file_version": rules.file_version,
        "up_axis": info.up_axis,
        "unit_scale_cm": info.unit_scale_cm,
    })


def write_report(report: Report, out_file: Path) -> None:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": identical bytes on Windows and Linux.
    with out_file.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(report.to_json())
    print("%-5s %s %s" % (report.status.value.upper(), out_file,
                          json.dumps(report.summary(), sort_keys=True)))


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
        from validate.maya import registry  # needs Maya running (checks import maya)
        unknown = registry.unknown_override_ids(conventions)
        if unknown:
            print("artpipe: mesh_budgets.yaml severity_overrides name unknown checks: %s"
                  % "; ".join(unknown), file=sys.stderr)
            return EXIT_TOOL_ERROR

        args.out.mkdir(parents=True, exist_ok=True)
        worst = 0
        for path in args.files:
            fix_dir = args.fixed_dir if args.fix else None
            report = validate_file(path, conventions, asset_class, args.max_offenders, fix_dir)
            # Full file name, not stem: SM_Crate.ma and SM_Crate.fbx must not
            # overwrite each other's report.
            write_report(report, args.out / (path.name + ".report.json"))
            worst = max(worst, report.exit_code)
            if args.export is None:
                continue
            if report.exit_code != 0:
                print("SKIP  export of %s: it did not pass" % path.name)
                continue
            exported = export_file(validated_file(path, report, fix_dir), report, conventions,
                                   asset_class, args.export, args.max_offenders)
            write_report(exported, args.export / (path.stem + ".fbx.report.json"))
            worst = max(worst, exported.exit_code)
        return worst
    finally:
        session.stop()


if __name__ == "__main__":
    sys.exit(main())
