# The MCP agent layer (Phase 5)

artpipe as tools an agent can call. The agent (Claude Code today, Sophia
later) is the MCP client and makes every judgement call; artpipe stays
deterministic and never calls an LLM. Scope agreed on 2026-10-20 (see
`CLAUDE.md`).

## The server (step 5.1)

```powershell
.\artpipe.cmd agent setup     # once: the server's venv (Python 3.12 + the locked MCP SDK)
.\artpipe.cmd agent status    # JSON: venv, Python, installed mcp, lock hash
```

`.mcp.json` at the repo root registers it for Claude Code
(`cmd /d /c artpipe.cmd agent serve`, stdio). Open the repo in Claude Code
and run `/mcp` to see it.

**A thin front end.** The server (`agent/server.py`) runs each tool as an
artpipe command (`agent/core/commands.py`) and returns its exit code, its
meaning and what it wrote. It holds no pipeline logic: the agent sees what
a human at the CLI sees, and the rules are applied only by the tested
Python 3.9 code.

**Its own Python.** The official MCP SDK needs Python 3.10+, mayapy is 3.9.
`artpipe agent setup` builds `%LOCALAPPDATA%\artpipe\agent-venv` (or
`ARTPIPE_TOOLS`) from the py launcher's 3.12 and `agent/requirements.lock`:
30 wheels, exact versions and sha256, installed with `--require-hashes
--no-deps` (no resolver, no newer transitive dependency). The lock is made
by `agent/lock.py` from downloaded wheels; it is for CPython 3.12 on
win_amd64. PyYAML is the same 6.0.2 mayapy uses, so both read the rules
alike. The SDK is pinned to 2.2.0 (released 2026-09-07; 2.3.0 was 18 days
old). A test checks that `agent.yaml`, the lock, `artpipe.cmd` and
`.mcp.json` agree.

Probed while building it:

| Probe | Result |
|---|---|
| `mcp` 2.x API | `FastMCP` is gone (renamed `MCPServer`, `mcp.server.mcpserver`), and models are snake_case (`input_schema`, `is_error`, `structured_content`): v1 examples don't run |
| Spawning the server with the SDK's stdio client | **the server gets a minimal environment**: the SDK passes 12 variables on Windows (APPDATA, HOMEDRIVE, HOMEPATH, LOCALAPPDATA, PATH, PATHEXT, PROCESSOR_ARCHITECTURE, SYSTEMDRIVE, SYSTEMROOT, TEMP, USERNAME, USERPROFILE). No `ProgramFiles`, so `artpipe.cmd` couldn't find mayapy ("mayapy not found") and no `COMSPEC`. `artpipe.cmd` now falls back to the defaults. Your own `MAYAPY`, `UNITY` or `ARTPIPE_TOOLS` must go in the `env` of `.mcp.json`: they won't be inherited |
| `subprocess.run(timeout=3)` on an artpipe command that sleeps 25 s | **returned after 25 s**, and mayapy kept running: it kills `cmd.exe`, then waits for the pipes, which the mayapy child still holds. The runner now kills the whole tree (`taskkill /T /F`): 3.2 s, no orphan |
| A real call over stdio (`artpipe_info`) | connect ~1.1 s, call ~0.35 s (mayapy without Maya loaded is fast) |

**Arguments are data, never syntax.** Every tool argument reaches
`cmd.exe`, so `& | < > ^ % "` and newlines are refused before anything runs
(`commands.check_args`): a prompt like `a coin & del *` can't become a
second command.

The 5.1 tool: `artpipe_info` (repo commit, every conventions file with its
sha256, the agent pins, the banner renderer's tool status). Call it first.

Tests: `tests/test_agent_core.py` (rules, the argument filter, a real
command, the timeout killing the tree) and `tests/test_agent_server.py`
(marker `agent`: the real server over stdio from the SDK's client, minimal
environment included; removing the `ProgramFiles` fallback turns it red).
