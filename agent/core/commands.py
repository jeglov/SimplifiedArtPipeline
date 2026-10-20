"""Run an artpipe command the way a human would at the prompt, and capture what it says.

Every tool of the MCP server is an artpipe command: `artpipe.cmd ARGS...`
run from the repo root. The server never imports the pipeline's code (it
runs under another Python); it reads the command's exit code, its one-line
verdict and the report files the command wrote.

Stdlib only, Python 3.9.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
ARTPIPE = ROOT / "artpipe.cmd"
EXIT_MEANING = {0: "pass", 1: "the asset failed its checks (or was handed over)", 2: "tool error"}


@dataclass(frozen=True)
class Outcome:
    argv: List[str]          # what was run, relative to the repo (no machine path)
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def meaning(self) -> str:
        return "timed out" if self.timed_out else EXIT_MEANING.get(self.exit_code, "unknown exit code")

    def to_dict(self, tail: int = 4000) -> Dict[str, object]:
        return {"command": "artpipe " + " ".join(self.argv), "exit_code": self.exit_code, "meaning": self.meaning,
                "stdout": self.stdout[-tail:], "stderr": self.stderr[-tail:], "timed_out": self.timed_out}


def argv_for(args: Sequence[str]) -> List[str]:
    """The process argv, for display and tests: cmd.exe runs the batch file (a .cmd can't be exec'd)."""
    return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", str(ARTPIPE)] + [str(a) for a in args]


def cmdline_for(args: Sequence[str]) -> str:
    """The exact Windows command line: every argument quoted, the whole wrapped for `cmd /s /c`.

    Probed: a batch file splits an UNQUOTED argument on , ; = (and spaces):
    `--checks a,b` reached the validator as two arguments. Python's own
    quoting (list2cmdline) quotes only arguments with spaces, so the line is
    built here: each argument in double quotes (check_args has refused any
    quote inside one), and the command wrapped in one more pair of quotes,
    which /s tells cmd.exe to strip and nothing else.
    """
    inner = " ".join('"%s"' % a for a in [str(ARTPIPE)] + [str(x) for x in args])
    return '%s /d /s /c "%s"' % (os.environ.get("COMSPEC", "cmd.exe"), inner)


def check_args(args: Sequence[str]) -> None:
    """Refuse arguments cmd.exe would interpret: a tool argument must never become a second command."""
    for a in args:
        s = str(a)
        if any(c in s for c in '&|<>^%"\r\n'):
            raise ValueError("argument %r contains a character cmd.exe treats as syntax (& | < > ^ %% \" or a "
                             "newline)" % s)
        if s == "":
            raise ValueError("an empty argument can't be passed to a batch file (it ends the argument list)")


def kill_tree(pid: int) -> None:
    """End a process and every process it started (cmd.exe -> mayapy -> node -> chrome ...)."""
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run(args: Sequence[str], timeout_s: float, env: Optional[Mapping[str, str]] = None,
        cwd: Path = ROOT) -> Outcome:
    """Run `artpipe ARGS`. On timeout the whole process tree is killed.

    Probed: subprocess.run(timeout=3) on `artpipe py -c "sleep(25)"` returned
    after 25 s, not 3. It kills cmd.exe, then waits for the output pipes, and
    the mayapy child still holds them open (and keeps running). A hung render
    would block a tool forever.
    """
    check_args(args)
    proc = subprocess.Popen(cmdline_for(args), cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, env=dict(env or os.environ),
                            universal_newlines=True, encoding="utf-8", errors="replace")
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)
        out, err = proc.communicate()
        return Outcome([str(a) for a in args], 2, out or "", err or "", timed_out=True)
    return Outcome([str(a) for a in args], proc.returncode, out, err)


def run_to_files(args: Sequence[str], timeout_s: float, stdout_path: Path, stderr_path: Path,
                 env: Optional[Mapping[str, str]] = None, cwd: Path = ROOT) -> Tuple[int, bool]:
    """Like run(), but the output streams into files as it is printed (a job's live log). (exit code, timed out)."""
    check_args(args)
    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        proc = subprocess.Popen(cmdline_for(args), cwd=str(cwd), stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                                env=dict(env or os.environ))
        try:
            return proc.wait(timeout=timeout_s), False
        except subprocess.TimeoutExpired:
            kill_tree(proc.pid)
            proc.wait()
            return 2, True

