"""Local Animate authoring tools; startup and status never launch Animate."""

import threading
from typing import Literal

import anyio.to_thread
from cavi_mcp_core.server import READ_ONLY as READ
from cavi_mcp_core.server import WRITES as WRITE
from cavi_mcp_core.server import Server
from mcp.server.mcpserver.exceptions import ToolError

from . import animate_authoring
from .config import workspace
from .contracts import Result
from .timeline_models import TimelineBatch

mcp = Server("cavi-animate-authoring", package="cavi-adobe-mcp", anticipated=(), instructions=(
    "Inspect and author only workspace-owned Adobe Animate FLA copies. Animate must already be open. "
    "Commands use a fixed JSFL driver and typed data; native unknown outcomes block retries. "
    "Server startup and status never launch Animate."
))


class AnimateSnapshot(Result):
    native: dict
    pending_operation: dict | None
    launches_application: Literal[False]


class AnimateOperation(Result):
    operation_id: str
    session: str
    document: dict
    retry_safe: Literal[False]


class AnimateStatus(Result):
    application: str
    version: str
    running: bool
    launches_application: Literal[False]


async def invoke(function, *args, cancellable=False):
    cancelled = threading.Event()
    try:
        return await anyio.to_thread.run_sync(
            lambda: function(*args, **({"cancelled": cancelled} if cancellable else {})),
            abandon_on_cancel=True)
    except (OSError, ValueError, TypeError, KeyError, animate_authoring.AnimateUnknown) as exc:
        raise ToolError(str(exc)) from exc
    except BaseException:
        cancelled.set()
        raise


def host():
    return animate_authoring.AnimateAuthoring(workspace())


@mcp.tool(annotations=READ)
async def animate_status() -> AnimateStatus:
    """Inspect installed Animate and process state without launching the app."""
    return await invoke(host().status)


@mcp.tool(annotations=READ)
async def animate_inspect() -> AnimateSnapshot:
    """Read open document, timeline, layers and symbols; report any unresolved operation."""
    return await invoke(host().inspect)


@mcp.tool(annotations=WRITE)
async def animate_open_copy(source: str, destination: str) -> AnimateOperation:
    """Copy a workspace FLA to a new path and open only that owned copy."""
    return await invoke(host().open_copy, source, destination, cancellable=True)


@mcp.tool(annotations=WRITE)
async def animate_create_from_png(source: str, destination: str) -> AnimateOperation:
    """Create an editable timeline FLA from one workspace RGBA PNG, saved to a new path."""
    return await invoke(host().create_from_png, source, destination, cancellable=True)


@mcp.tool(annotations=WRITE)
async def animate_set_stage_size_from_source(session_id: str) -> AnimateOperation:
    """Set the owned FLA stage to its unchanged RGBA source image dimensions."""
    return await invoke(host().set_stage_size_from_source, session_id, cancellable=True)


@mcp.tool(annotations=WRITE)
async def animate_export_frames(session_id: str, directory: str) -> AnimateOperation:
    """Export the owned document's explicitly ordered timeline frames to a new directory and media receipt."""
    return await invoke(host().export_frames, session_id, directory, cancellable=True)


@mcp.tool(annotations=WRITE)
async def animate_edit_timeline(session_id: str, batch: TimelineBatch) -> AnimateOperation:
    """Author named layers, duration, keyframes, matrices, library symbols and classic tweens; save editable FLA."""
    return await invoke(host().edit_timeline, session_id,
                        batch.model_dump(exclude_none=True)["edits"], cancellable=True)


@mcp.tool(annotations=WRITE)
async def animate_close_copy(session_id: str) -> AnimateOperation:
    """Save and close only the exact owned FLA copy."""
    return await invoke(host().close_copy, session_id, cancellable=True)


class AnimateRecovery(Result):
    state: str
    operation_id: str
    session: str
    retry_safe: Literal[False]
    completion_available: bool | None = None
    can_reconcile: bool | None = None
    copy_sha256: str | None = None
    native_sha256: str | None = None
    document: dict | None = None


@mcp.tool(annotations=WRITE)
async def animate_operation_control(session_id: str, operation_id: str,
        action: Literal["inspect", "adopt_current", "cancel_unsubmitted", "abandon_after_exit"] = "inspect",
        expected_copy_sha256: str | None = None, expected_native_sha256: str | None = None) -> AnimateRecovery:
    """Inspect retained native completion and explicitly adopt observed copy hashes without replaying the command."""
    return await invoke(host().operation_control, action, session_id, operation_id,
                        expected_copy_sha256, expected_native_sha256, cancellable=True)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
