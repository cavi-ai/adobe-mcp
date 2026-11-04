"""Typed MCP entry points for bounded Adobe Dimension renders."""

from typing import Any, Literal

import anyio.to_thread
from cavi_mcp_core.server import READ_ONLY as READ
from cavi_mcp_core.server import WRITES as WRITE
from cavi_mcp_core.server import Server
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import dimension_authoring
from .config import workspace
from .contracts import Result

mcp = Server("cavi-adobe-dimension", package="cavi-adobe-mcp", anticipated=(), instructions=(
    "Inspect an already-running Adobe Dimension instance or submit one workspace-owned scene render. "
    "Render submission is not completion; inspect the same operation id to verify the PNG. "
    "Status and startup never launch the app; dimension_launch explicitly opens the fixed installed app."
))
RENDER_INSPECT = ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                 idempotentHint=True, openWorldHint=False)


class DimensionObservation(Result):
    native: dict[str, Any]
    launches_application: Literal[False]


class DimensionRenderStarted(Result):
    operation_id: str
    status: Literal["render_requested"]
    output: str
    directory: str
    owned_copy: str
    captures: list[str]
    source_identity: Literal["opened_owned_copy"]
    input_sha256: str
    owned_copy_sha256: str
    process_identity: dict[str, Any]
    retry_safe: Literal[False]


class DimensionRenderInspection(Result):
    operation_id: str
    status: Literal["pending", "complete", "unsupported", "rejected"]


class DimensionLaunchResult(Result):
    status: Literal["launched", "already_running"]
    process_identity: dict[str, Any]
    bundle_id: Literal["com.adobe.dimension"]
    bundle_path: Literal["/Applications/Adobe Dimension/Adobe Dimension.app"]
    launches_application: bool


def host():
    return dimension_authoring.DimensionAuthoring(workspace())


async def invoke(function):
    try:
        return await anyio.to_thread.run_sync(function)
    except (dimension_authoring.DimensionRejected, dimension_authoring.DimensionUnknown,
            OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=READ)
async def dimension_status() -> DimensionObservation:
    """Read installed Dimension and process state without launching the app."""
    return {"native": await invoke(host().status), "launches_application": False}


@mcp.tool(annotations=WRITE)
async def dimension_launch() -> DimensionLaunchResult:
    """Open the fixed installed Dimension app once, reusing an existing process."""
    return await invoke(host().launch)


@mcp.tool(annotations=READ)
async def dimension_inspect() -> DimensionObservation:
    """Read visible Dimension windows and accessibility controls; perform no app actions."""
    return {"native": await invoke(host().inspect), "launches_application": False}


@mcp.tool(annotations=WRITE)
async def dimension_render_start(scene: str, directory: str) -> DimensionRenderStarted:
    """Render a saved workspace .dn into a new directory using visible Dimension controls."""
    return await invoke(lambda: host().render_start(scene, directory))


@mcp.tool(annotations=RENDER_INSPECT)
async def dimension_render_inspect(operation_id: str) -> DimensionRenderInspection:
    """Verify the render job; absent output remains pending and completion retains a receipt."""
    return await invoke(lambda: host().render_inspect(operation_id))


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
