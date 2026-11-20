"""Where the servers read and write: one workspace."""

from cavi_mcp_core.workspace import root

WORKSPACE = "CAVI_ADOBE_WORKSPACE"


def workspace() -> str:
    """The absolute workspace every tool path is relative to, from ``CAVI_ADOBE_WORKSPACE``."""
    return str(root(WORKSPACE))
