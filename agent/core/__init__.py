"""Pure agent-layer logic (rules, commands, jobs, the approval queue).

Python 3.9-compatible and free of the MCP SDK, so `artpipe test` (mayapy)
tests it; only agent/server.py imports `mcp`.
"""


class ToolError(Exception):
    """The agent layer could not do its job (exit 2). Never means "the asset is bad"."""
