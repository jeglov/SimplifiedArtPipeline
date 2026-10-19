"""Run the Unity side of artpipe headless (batchmode) and normalise its result.

    artpipe unity setup                                one-time project setup (URP, Linear, text assets)
    artpipe unity import --class C FILE... [--report]  gate, copy into Assets/Art/C/, import, measure
    artpipe unity verify [--report PATH]               re-check every managed asset, no import cache
    artpipe unity test [--results PATH]                EditMode tests (Unity Test Framework)

FILE: an FBX exported by `artpipe validate --export` (its X.fbx.report.json
sidecar must sit next to it) or a texture (T_<Name>_<D|N|MS|AO|E>.png|tga).

Same exit contract as the Maya CLI: 0 pass / 1 asset failed / 2 tool error.

Why a Python wrapper instead of calling Unity.exe directly: Unity's own exit
code is ambiguous. An unhandled exception, a compile error (the -executeMethod
target doesn't exist) or a second Editor holding the project lock all make
Unity exit with 1, which our contract reads as "the asset failed". So the
wrapper deletes the old report, runs Unity, and trusts the result only if a
fresh report exists AND its status agrees with Unity's exit code. Anything
else is a tool error (2) pointing at the log.

Stays Python 3.9-compatible and never imports Maya (it runs under mayapy via
artpipe.cmd, but any Python works).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Mapping, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "unity"
OUT = ROOT / "out" / "unity"

EXIT_PASS, EXIT_FAIL, EXIT_TOOL_ERROR = 0, 1, 2
STATUS_EXIT = {"pass": EXIT_PASS, "fail": EXIT_FAIL, "error": EXIT_TOOL_ERROR}

# Unity's documented -runTests exit codes.
TESTS_EXIT = {0: EXIT_PASS, 2: EXIT_FAIL}

# A cold Editor start plus a first import can take minutes; a hung Editor must
# still end as a tool error instead of blocking the agent forever.
TIMEOUT_S = 30 * 60

# The ONLY build target the pipeline imports for: Unity compresses textures
# for the active target, and the active target isn't stored in the project.
BUILD_TARGET = "Android"


class ToolError(Exception):
    pass


def editor_version(project: Path = PROJECT) -> str:
    """The exact Unity version the project pins (ProjectSettings/ProjectVersion.txt)."""
    path = project / "ProjectSettings" / "ProjectVersion.txt"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("m_EditorVersion:"):
                return line.split(":", 1)[1].strip()
    except OSError as exc:
        raise ToolError("cannot read %s: %s" % (path, exc))
    raise ToolError("no m_EditorVersion in %s" % path)


def find_unity(env: Mapping[str, str] = os.environ, project: Path = PROJECT) -> Path:
    """%UNITY% if set, otherwise the Hub install of the version the project pins.

    Not "the newest Unity": opening a project in another version upgrades it
    (and rewrites files), which must never happen silently in a pipeline.
    """
    if env.get("UNITY"):
        exe = Path(env["UNITY"])
        if not exe.is_file():
            raise ToolError("UNITY points to a missing file: %s" % exe)
        return exe
    version = editor_version(project)
    base = Path(env.get("ProgramFiles", r"C:\Program Files"))
    exe = base / "Unity" / "Hub" / "Editor" / version / "Editor" / "Unity.exe"
    if not exe.is_file():
        raise ToolError("Unity %s not found at %s. Install it with Unity Hub, or set UNITY "
                        "to the full path of Unity.exe" % (version, exe))
    return exe


def project_locked(project: Path = PROJECT, opener=open) -> bool:
    """True if a Unity Editor has the project open right now.

    Probed on Windows: a running Editor holds Temp/UnityLockfile open
    exclusively, so even reading it fails with PermissionError. A lockfile
    left behind by a crashed Editor opens fine, so it isn't a false positive.
    Checked before launching because Unity 6000.5 in batchmode exits 1 on a
    locked project without saying why in its log.
    """
    lockfile = project / "Temp" / "UnityLockfile"
    if not lockfile.is_file():
        return False
    try:
        opener(str(lockfile), "rb").close()
    except PermissionError:
        return True
    return False


def base_command(unity: Path, log: Path) -> List[str]:
    return [str(unity), "-batchmode", "-nographics",
            "-projectPath", str(PROJECT),
            "-buildTarget", BUILD_TARGET,
            "-logFile", str(log)]


def run_unity(cmd: Sequence[str]) -> int:
    try:
        return subprocess.run(list(cmd), timeout=TIMEOUT_S).returncode
    except subprocess.TimeoutExpired:
        raise ToolError("Unity did not finish within %d s" % TIMEOUT_S)


# Licence failures stop Unity before any of our code runs, so they can only
# be recognised from the log. (A working run also logs licensing noise such
# as "Access token is unavailable", so the patterns are deliberately narrow.)
_LICENCE = re.compile(r"No valid Unity Editor license|license (?:is )?(?:not active|has expired)"
                      r"|Failed to activate(?: the)? licen[cs]e", re.IGNORECASE)


def log_hint(log: Path) -> str:
    """A short, actionable hint for the failures we know how to recognise."""
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if "another Unity instance is running" in text:
        return " Another Unity Editor has this project open: close it and retry."
    if "Scripts have compiler errors" in text or "error CS" in text:
        return " The C# code doesn't compile: search the log for 'error CS'."
    if _LICENCE.search(text):
        return (" Unity has no usable licence: sign in to Unity Hub (Personal) or activate a"
                " licence for batchmode, then retry.")
    return ""


def reconcile(unity_rc: int, report: Path, log: Path) -> Tuple[int, str]:
    """(exit code, message) from Unity's exit code and the report it wrote."""
    where = " See %s." % rel(log)
    if not report.is_file():
        return EXIT_TOOL_ERROR, ("Unity exited with code %d and wrote no report.%s%s"
                                 % (unity_rc, log_hint(log), where))
    try:
        status = json.loads(report.read_text(encoding="utf-8"))["status"]
        code = STATUS_EXIT[status]
    except (ValueError, KeyError, TypeError) as exc:
        return EXIT_TOOL_ERROR, "unreadable report %s: %r.%s" % (rel(report), exc, where)
    if unity_rc != code:
        return EXIT_TOOL_ERROR, ("Unity exit code %d disagrees with report status %r.%s"
                                 % (unity_rc, status, where))
    return code, "%s %s" % (status.upper(), rel(report))


def summarize_tests(unity_rc: int, results: Path, log: Path) -> Tuple[int, str]:
    """(exit code, message) from a -runTests run and its NUnit 3 XML results."""
    where = " See %s." % rel(log)
    if not results.is_file():
        return EXIT_TOOL_ERROR, ("Unity exited with code %d and wrote no test results.%s%s"
                                 % (unity_rc, log_hint(log), where))
    try:
        run = ET.parse(str(results)).getroot()
        counts = {k: int(run.get(k, "0")) for k in ("total", "passed", "failed", "skipped")}
    except (ET.ParseError, ValueError) as exc:
        return EXIT_TOOL_ERROR, "unreadable test results %s: %r.%s" % (rel(results), exc, where)
    line = "%(total)d tests: %(passed)d passed, %(failed)d failed, %(skipped)d skipped" % counts
    expected = TESTS_EXIT.get(unity_rc)
    if counts["total"] == 0:
        # Zero tests is never a pass: the test assembly didn't compile or wasn't found.
        return EXIT_TOOL_ERROR, "no tests ran (%s).%s" % (rel(results), where)
    if expected is None or (expected == EXIT_PASS) != (counts["failed"] == 0):
        return EXIT_TOOL_ERROR, ("Unity exit code %d disagrees with results (%s).%s"
                                 % (unity_rc, line, where))
    return expected, "%s %s (%s)" % ("PASS" if expected == EXIT_PASS else "FAIL", line, rel(results))


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def absolute(path) -> Path:
    """Absolute path, existing or not. Not Path.resolve(): on Windows before
    Python 3.10 it returns a RELATIVE path for a file that doesn't exist yet
    (caught by a test under mayapy 3.9), and Unity needs absolute paths."""
    return Path(os.path.abspath(str(path)))


def fresh(path: Path) -> Path:
    """Delete a previous output so a stale file can never pass for a new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    return path


def cmd_setup(unity: Path, args: argparse.Namespace) -> Tuple[int, str]:
    log = fresh(OUT / "setup.log")
    rc = run_unity(base_command(unity, log) + ["-executeMethod", "ProjectSetup.Apply", "-quit"])
    if rc != 0:
        return EXIT_TOOL_ERROR, "project setup failed (Unity exit %d).%s See %s." % (
            rc, log_hint(log), rel(log))
    return EXIT_PASS, "project setup applied"


def cli_command(unity: Path, log: Path, report: Path, extra: Sequence[str] = ()) -> List[str]:
    return (base_command(unity, log)
            + ["-executeMethod", "Artpipe.Cli.Import", "-quit", "--report", str(report)] + list(extra))


def cmd_import(unity: Path, args: argparse.Namespace) -> Tuple[int, str]:
    report = fresh(absolute(args.report))
    log = fresh(OUT / "import.log")
    extra = ["--class", args.asset_class]
    for f in args.files:
        # Absolute: Unity's working directory is the project, not ours.
        extra += ["--file", str(absolute(f))]
    return reconcile(run_unity(cli_command(unity, log, report, extra)), report, log)


def cmd_verify(unity: Path, args: argparse.Namespace) -> Tuple[int, str]:
    report = fresh(absolute(args.report))
    log = fresh(OUT / "verify.log")
    return reconcile(run_unity(cli_command(unity, log, report)), report, log)


def cmd_test(unity: Path, args: argparse.Namespace) -> Tuple[int, str]:
    results = fresh(absolute(args.results))
    log = fresh(OUT / "test.log")
    # No -quit: -runTests quits by itself, and -quit would end Unity before
    # the tests run.
    rc = run_unity(base_command(unity, log)
                   + ["-runTests", "-testPlatform", "EditMode", "-testResults", str(results)])
    return summarize_tests(rc, results, log)


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="artpipe unity", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="one-time project setup (URP, Linear, text assets)")
    imp = sub.add_parser("import", help="gate, copy into Assets/Art/<class>/, import and measure")
    imp.add_argument("files", nargs="+", help="FBX (with its .fbx.report.json sidecar) or texture files")
    imp.add_argument("--class", dest="asset_class", required=True, help="destination class folder")
    imp.add_argument("--report", default=str(OUT / "import.report.json"))
    ver = sub.add_parser("verify", help="re-check every managed asset (no import cache)")
    ver.add_argument("--report", default=str(OUT / "verify.report.json"))
    tst = sub.add_parser("test", help="EditMode tests")
    tst.add_argument("--results", default=str(OUT / "editmode-results.xml"))
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        unity = find_unity()
        if project_locked():
            raise ToolError("a Unity Editor has %s open. Close it and retry (only one Unity "
                            "may open a project at a time)" % rel(PROJECT))
        commands = {"setup": cmd_setup, "import": cmd_import, "verify": cmd_verify, "test": cmd_test}
        code, message = commands[args.command](unity, args)
    except ToolError as exc:
        code, message = EXIT_TOOL_ERROR, str(exc)
    print("artpipe unity: %s" % message, file=sys.stdout if code == EXIT_PASS else sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
