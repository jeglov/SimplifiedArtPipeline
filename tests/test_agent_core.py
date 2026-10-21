"""agent.core: rules, the command runner, and the pins that must agree across files. ~10 s (real artpipe calls)."""
from __future__ import annotations

import copy
import json
import re
import subprocess
import time
from pathlib import Path

import pytest

from agent.core import commands
from agent.core import rules as ar
from validate.core.conventions import DEFAULT_DIR, ConventionError, _read_yaml

ROOT = Path(__file__).resolve().parents[1]
RAW = _read_yaml(DEFAULT_DIR / ar.FILE)
RULES = ar.load()


def test_shipped_rules_load():
    assert (RULES.python, RULES.mcp_version, RULES.venv) == ("3.12", "2.2.0", "agent-venv")
    assert RULES.server.call_timeout_s > 0


@pytest.mark.parametrize("key, value, match", [
    ("python", "3", "exact version"),
    ("mcp_version", "^2.2", "exact version"),
    ("venv", "../escape", "a folder name"),
    ("server", {"name": "artpipe", "call_timeout_s": 0}, "> 0"),
    ("extra", 1, "unknown key"),
])
def test_rule_mistakes_fail_at_load(key, value, match):
    data = copy.deepcopy(RAW)
    data[key] = value
    with pytest.raises(ConventionError, match=match):
        ar.parse(data)


def test_the_pins_agree_across_files():
    lock = (ROOT / RULES.lock).read_text(encoding="utf-8")
    assert "mcp==%s --hash=sha256:" % RULES.mcp_version in lock
    # The venv reads conventions with the same PyYAML as mayapy.
    maya_yaml = re.search(r"^pyyaml==(\S+)", (ROOT / "requirements-maya.txt").read_text(), re.M).group(1)
    assert "pyyaml==%s --hash=sha256:" % maya_yaml in lock
    assert all(" --hash=sha256:" in line for line in lock.splitlines() if line and not line.startswith("#"))
    # artpipe.cmd starts the server from the venv folder agent.yaml names.
    assert "\\%s\\Scripts\\python.exe" % RULES.venv in (ROOT / "artpipe.cmd").read_text(encoding="ascii")
    mcp = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"][RULES.server.name]
    assert mcp["args"][-2:] == ["agent", "serve"] and mcp["args"][-3].endswith("artpipe.cmd")
    # Claude Code sets NoDefaultCurrentDirectoryInExePath=1: cmd.exe then won't
    # find a bare "artpipe.cmd" in the current folder, and the server never starts.
    assert mcp["args"][-3] == ".\\artpipe.cmd" or Path(mcp["args"][-3]).is_absolute()


@pytest.mark.parametrize("bad", ["a & del x", "a | b", "x > y", "x < y", "50%", "a^b", 'say "hi"', "a\nb"])
def test_cmd_syntax_in_an_argument_is_refused(bad):
    with pytest.raises(ValueError, match="cmd.exe"):
        commands.check_args(["banners", bad])


def test_ordinary_arguments_pass():
    commands.check_args(["generate", "a red toadstool, white spots (3/4 view)", "--class", "pickup", "C:/x y/z.ma"])


def test_argv_runs_the_batch_file_through_cmd():
    argv = commands.argv_for(["banners", "status"])
    assert argv[1:4] == ["/d", "/s", "/c"] and argv[4].endswith("artpipe.cmd") and argv[-2:] == ["banners", "status"]
    line = commands.cmdline_for(["banners", "a,b"])
    assert line.endswith('"%s" "banners" "a,b""' % commands.ARTPIPE)


def test_commas_semicolons_equals_and_spaces_arrive_intact():
    # Probed: a batch file splits an unquoted argument on , ; = : "--checks a,b" arrived as two.
    weird = ["a,b", "x;y", "k=v", "two words", "a, red, toadstool", "(3/4 view)", "tab\tnot", "!bang"]
    out = commands.run(["py", "-c", "import sys, json; print(json.dumps(sys.argv[1:]))"] + weird, timeout_s=60)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == weird


def test_an_empty_argument_is_refused():
    with pytest.raises(ValueError, match="empty argument"):
        commands.check_args(["validate", ""])


def test_run_a_real_command():
    out = commands.run(["banners", "status"], timeout_s=60)
    assert out.exit_code in (0, 2) and not out.timed_out
    d = out.to_dict()
    assert d["command"] == "artpipe banners status" and str(ROOT) not in d["command"]


def _mayapy_running(marker):
    ps = subprocess.run(["powershell", "-NoProfile", "-Command",
                         "Get-CimInstance Win32_Process -Filter \"Name='mayapy.exe'\" | "
                         "Select-Object -ExpandProperty CommandLine"],
                        stdout=subprocess.PIPE, universal_newlines=True).stdout
    return marker in ps


def test_timeout_kills_the_whole_tree_and_returns_on_time():
    marker = "artpipe_timeout_probe_%d" % int(time.time() * 1000)
    started = time.time()
    out = commands.run(["py", "-c", "import time; time.sleep(30)  # " + marker], timeout_s=3)
    took = time.time() - started
    assert out.timed_out and out.exit_code == 2 and out.meaning == "timed out"
    assert took < 10, "returned after %.1f s: the timeout waited for the child" % took
    time.sleep(1)
    assert not _mayapy_running(marker), "mayapy outlived the timeout"
