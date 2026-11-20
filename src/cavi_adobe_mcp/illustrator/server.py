"""Explicit native Illustrator authoring on owned AI copies; lazy app access."""

from typing import Annotated, Literal

from cavi_mcp_core.contracts import Result
from cavi_mcp_core.server import DESTRUCTIVE as MUTATE
from cavi_mcp_core.server import READ_ONLY as READ
from cavi_mcp_core.server import WRITES as WRITE
from cavi_mcp_core.server import Server
from pydantic import Field

from ..core.config import workspace
from ..core.owned_tools import (
    AdobeExport,
    AdobeFonts,
    AdobeMutation,
    AdobeRecovery,
    AdobeSnapshot,
    BezierPoint,
    LayerEdit,
    LayerPath,
    invoke,
)
from . import bridge

mcp = Server("cavi-adobe-illustrator", package="cavi-adobe-mcp", anticipated=(), instructions=(
    "Explicit local Illustrator edits on new owned AI copies. Server startup never launches "
    "Illustrator. Start with illustrator_status; native document operations require an "
    "already running app and local Automation access. Never sign in or retry an unknown "
    "outcome. Keep .adobe-authoring state private."
))


class IllustratorStatus(Result):
    app: Literal["illustrator"]
    application: str
    version: str
    running: bool
    launches_application: Literal[False]
    authenticates: Literal[False]


def host():
    return bridge.IllustratorAuthoring(workspace())


@mcp.tool(annotations=READ)
def illustrator_status() -> IllustratorStatus:
    """Inspect installation and running process only; no app launch or Apple events."""
    return invoke(host().status)


@mcp.tool(annotations=READ)
def illustrator_inspect(session_id: str | None = None) -> AdobeSnapshot:
    """Read native document/layer identities; an unknown command blocks later mutations."""
    owned = host()
    if session_id is None:
        return invoke(owned.inspect)
    result = invoke(owned.inspect_session, session_id)
    return {**result["native"], "session": result["session"]}


@mcp.tool(annotations=READ)
def illustrator_fonts() -> AdobeFonts:
    """List native font families, styles and exact PostScript names."""
    return invoke(host().fonts)


@mcp.tool(annotations=WRITE)
def illustrator_open_copy(source: str, destination: str) -> AdobeMutation:
    """Open a new owned AI copy from a workspace AI, SVG or PNG file. Destination must be new."""
    return invoke(host().open_copy, source, destination)


@mcp.tool(annotations=MUTATE)
def illustrator_edit_layers(session_id: str,
                            edits: Annotated[list[LayerEdit], Field(min_length=1, max_length=64)]) -> AdobeMutation:
    """Rename, hide/show or change opacity of exact paths with expected names in an owned copy."""
    return invoke(host().edit_layers, session_id,
                  [item.model_dump(exclude_none=True) for item in edits])


@mcp.tool(annotations=MUTATE)
def illustrator_add_layer(session_id: str, name: str, parent_path: LayerPath | None = None,
                          expected_parent_name: str | None = None) -> AdobeMutation:
    """Create a named layer, or a sublayer inside an exact named layer, for editable motifs or layered source parts."""
    return invoke(host().add_group, session_id, name, parent_path=parent_path,
                  expected_parent_name=expected_parent_name)


@mcp.tool(annotations=MUTATE)
def illustrator_place_image(session_id: str, source: str, name: str,
                            position: Annotated[list[float], Field(min_length=2, max_length=2)],
                            group_path: LayerPath | None = None,
                            expected_group_name: str | None = None) -> AdobeMutation:
    """Embed an existing PNG on a new layer in a named layer; x/y are points from the top left of artboard 1."""
    return invoke(host().place_image, session_id, source, name, position,
                  group_path=group_path, expected_group_name=expected_group_name)


@mcp.tool(annotations=MUTATE)
def illustrator_add_rectangle(session_id: str, name: str,
                              bounds: Annotated[list[float], Field(min_length=4, max_length=4)],
                              rgb: Annotated[list[float], Field(min_length=3, max_length=3)],
                              opacity: Annotated[float, Field(ge=0, le=100)] = 100) -> AdobeMutation:
    """Add an unstroked filled vector rectangle on a new layer; bounds are x,y,width,height in pt."""
    return invoke(host().add_rectangle, session_id, name, bounds, rgb, opacity)


@mcp.tool(annotations=MUTATE)
def illustrator_add_path(session_id: str, name: str,
                         points: Annotated[list[BezierPoint], Field(min_length=2, max_length=1024)],
                         closed: bool = True, rgb: list[float] | None = None,
                         group_path: LayerPath | None = None,
                         expected_group_name: str | None = None) -> AdobeMutation:
    """Create retained editable Bézier geometry on a new sublayer, optionally filled; pt from the top left of artboard 1."""
    return invoke(host().add_path, session_id, name,
                  [p.model_dump() for p in points], closed=closed, rgb=rgb,
                  group_path=group_path, expected_group_name=expected_group_name)


@mcp.tool(annotations=MUTATE)
def illustrator_add_text(session_id: str, name: str, text: str, font: str,
                         position: list[float], size: float, rgb: list[float]) -> AdobeMutation:
    """Create an editable text frame using an exact font name from illustrator_fonts; size in points."""
    return invoke(host().add_text, session_id, name, text, font, position, size, rgb)


@mcp.tool(annotations=MUTATE)
def illustrator_apply_mask(session_id: str, layer_path: LayerPath, expected_name: str,
                           path_name: str) -> AdobeMutation:
    """Clip the direct artwork of an exact layer with a duplicate of one retained closed named path."""
    return invoke(host().apply_mask, session_id, layer_path, expected_name, path_name)


@mcp.tool(annotations=WRITE)
def illustrator_export(session_id: str, destination: str, kind: Literal["png", "svg"] = "svg",
                       usage: Literal["color", "data"] = "color") -> AdobeExport:
    """Export the single artboard as vector SVG (default, color only) or PNG24; color usage needs an sRGB document."""
    return invoke(host().export, session_id, destination, kind=kind, usage=usage)


@mcp.tool(annotations=MUTATE)
def illustrator_close_copy(session_id: str) -> AdobeMutation:
    """Close only the exact cooperatively owned copy without saving; every earlier operation already saved it."""
    return invoke(host().close_copy, session_id)


@mcp.tool(annotations=MUTATE)
def illustrator_operation_control(session_id: str, operation_id: str,
        action: Literal["inspect", "adopt_current", "abandon_after_exit"] = "inspect",
        expected_copy_sha256: str | None = None, expected_native_sha256: str | None = None) -> AdobeRecovery:
    """Inspect retained completion; adopt a saved owned copy with both observed hashes, or abandon after app exit."""
    return invoke(host().operation_control, action, session_id, operation_id,
                  expected_copy_sha256, expected_native_sha256)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
