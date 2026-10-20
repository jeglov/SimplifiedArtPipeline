"""Pure generation logic. Never imports Maya, never talks to the network."""


class ToolError(Exception):
    """The generator could not do its job (exit 2). Never means "the asset is bad"."""
