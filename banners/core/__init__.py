"""Pure banner logic. Never imports Maya, never starts a browser or an encoder."""


class ToolError(Exception):
    """The banner tooling could not do its job (exit 2). Never means "the banner is bad"."""
