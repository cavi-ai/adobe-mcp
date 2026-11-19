"""Where the servers read and write: one workspace and one private state directory."""

import os
from pathlib import Path

from cavi_mcp_core.workspace import root

WORKSPACE = "CAVI_ADOBE_WORKSPACE"
DATA = "CAVI_ADOBE_DATA"


def workspace() -> str:
    """The absolute workspace every tool path is relative to, from ``CAVI_ADOBE_WORKSPACE``."""
    return str(root(WORKSPACE))


def data_dir() -> Path:
    """Private server state (helper builds, Character Animator journals), from ``CAVI_ADOBE_DATA``."""
    value = os.environ.get(DATA) or Path.home() / ".local/share/cavi-adobe-mcp"
    return Path(os.path.abspath(Path(value).expanduser()))
