"""Explicit native Adobe authoring and local review exports; lazy app access."""

from typing import Annotated, Literal

from cavi_mcp_core.server import DESTRUCTIVE as MUTATE
from cavi_mcp_core.server import READ_ONLY as READ
from cavi_mcp_core.server import WRITES as WRITE
from cavi_mcp_core.server import Server
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from . import adobe_authoring, native_media
from .config import workspace
from .contracts import Result

mcp = Server("cavi-adobe-authoring", package="cavi-adobe-mcp", anticipated=(), instructions=(
    "Explicit local Photoshop/Illustrator edits on new owned PSD/AI copies "
    "and FFmpeg review movies. Server startup never launches applications. Start with status; "
    "native Adobe document operations require an already running app and local Automation access. "
    "Never sign in or retry an unknown outcome. Keep .adobe-authoring state private."
))
App = Literal["photoshop", "illustrator"]
Index = Annotated[StrictInt, Field(ge=0, le=10000)]
LayerPath = Annotated[list[Index], Field(min_length=1, max_length=32)]


class LayerEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: LayerPath
    expected_name: str
    name: str | None = None
    visible: bool | None = None
    opacity: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None = None


class AdobeStatus(Result):
    app: App
    application: str
    version: str
    running: bool
    launches_application: Literal[False]
    authenticates: Literal[False]


class AdobeSnapshot(Result):
    runtime: dict
    pending_operation: dict | None


class AdobeMutation(Result):
    session_id: str
    operation_id: str
    document: dict
    copy_: dict = Field(alias="copy")
    source_sha256: str
    retry_safe: Literal[False]


class AdobeCanvas(AdobeMutation):
    source_kind: Literal["canvas_recipe"]
    source_sha256_kind: Literal["canvas_recipe_json"]
    canvas_recipe: dict


class MovieExport(Result):
    path: str
    sha256: str
    codec: Literal["prores_alpha", "h264_black"]
    stream: dict
    execution: dict
    visual_acceptance: Literal["unreviewed"]
    launches_applications: Literal[True]


def invoke(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (OSError, ValueError, adobe_authoring.AdobeOutcomeUnknown,
            native_media.MediaOutcomeUnknown) as exc:
        raise ToolError(str(exc)) from exc


def app(kind):
    return adobe_authoring.AdobeAuthoring(workspace(), kind)


@mcp.tool(annotations=READ)
def adobe_status(application: App) -> AdobeStatus:
    """Inspect installation and running process only; no app launch or Apple events."""
    return invoke(app(application).status)


@mcp.tool(annotations=READ)
def adobe_inspect(application: App, session_id: str | None = None) -> AdobeSnapshot:
    """Read native document/layer identities; an unknown command blocks later mutations."""
    host = app(application)
    if session_id is None:
        return invoke(host.inspect)
    result = invoke(host.inspect_session, session_id)
    return {**result["native"], "session": result["session"]}


@mcp.tool(annotations=WRITE)
def adobe_open_copy(application: App, source: str, destination: str) -> AdobeMutation:
    """Open a new owned PSD/AI copy from workspace PSD/AI/PNG/SVG. Destination must be new."""
    return invoke(app(application).open_copy, source, destination)


@mcp.tool(annotations=WRITE)
def adobe_create_canvas(width: Annotated[StrictInt, Field(ge=1, le=2880)],
                        height: Annotated[StrictInt, Field(ge=1, le=2880)],
                        destination: str, bit_depth: Literal[8, 16] = 16) -> AdobeCanvas:
    """Create a transparent 72 ppi sRGB Photoshop canvas; default depth is 16-bit."""
    return invoke(app("photoshop").create_canvas, width, height, destination, bit_depth)


@mcp.tool(annotations=MUTATE)
def adobe_resize_image(application: Literal["photoshop"], session_id: str,
                       width: Annotated[StrictInt, Field(ge=1, le=2880)],
                       height: Annotated[StrictInt, Field(ge=1, le=2880)]) -> AdobeMutation:
    """Resample an owned Photoshop PSD to pixel dimensions while retaining editable layers and paths."""
    return invoke(app(application).resize_image, session_id, width, height)


@mcp.tool(annotations=MUTATE)
def adobe_edit_layers(application: App, session_id: str,
                      edits: Annotated[list[LayerEdit], Field(min_length=1, max_length=64)]) -> AdobeMutation:
    """Rename, hide/show or change opacity of exact paths with expected names in an owned copy."""
    return invoke(app(application).edit_layers, session_id,
                  [item.model_dump(exclude_none=True) for item in edits])


@mcp.tool(annotations=MUTATE)
def adobe_add_group(application: App, session_id: str, name: str,
                    parent_path: LayerPath | None = None, expected_parent_name: str | None = None) -> AdobeMutation:
    """Create a named group for editable motifs or layered cartoon source parts."""
    return invoke(app(application).add_group, session_id, name, parent_path=parent_path,
                  expected_parent_name=expected_parent_name)


@mcp.tool(annotations=MUTATE)
def adobe_place_image(application: App, session_id: str, source: str, name: str,
                      position: Annotated[list[float], Field(min_length=2, max_length=2)],
                      group_path: LayerPath | None = None, expected_group_name: str | None = None) -> AdobeMutation:
    """Embed an existing PNG in a named group; x/y uses Photoshop pixels or Illustrator points."""
    return invoke(app(application).place_image, session_id, source, name, position,
                  group_path=group_path, expected_group_name=expected_group_name)


@mcp.tool(annotations=MUTATE)
def adobe_add_rectangle(application: App, session_id: str, name: str,
                        bounds: Annotated[list[float], Field(min_length=4, max_length=4)],
                        rgb: Annotated[list[float], Field(min_length=3, max_length=3)],
                        opacity: Annotated[float, Field(ge=0, le=100)] = 100) -> AdobeMutation:
    """Add a filled motif or mask on a new layer; bounds are x,y,width,height in px/pt."""
    return invoke(app(application).add_rectangle, session_id, name, bounds, rgb, opacity)


@mcp.tool(annotations=WRITE)
def adobe_export(application: App, session_id: str, destination: str,
                  kind: Literal["png", "svg"] | None = None, usage: Literal["color", "data"] = "color") -> AdobeMutation:
    """Export native-depth Photoshop PNG or vector Illustrator SVG by default; PNG24 is explicit."""
    return invoke(app(application).export, session_id, destination, kind=kind, usage=usage)


@mcp.tool(annotations=MUTATE)
def adobe_close_copy(application: App, session_id: str) -> AdobeMutation:
    """Save and close only the exact cooperatively owned copy; original documents are not targeted."""
    return invoke(app(application).close_copy, session_id)


class AdobeRecovery(Result):
    state: str
    operation_id: str
    session_id: str
    retry_safe: Literal[False]
    completion_available: bool | None = None
    can_reconcile: bool | None = None
    copy_sha256: str | None = None
    native_sha256: str | None = None
    document: dict | None = None


@mcp.tool(annotations=MUTATE)
def adobe_operation_control(application: App, session_id: str, operation_id: str,
        action: Literal["inspect", "adopt_current", "abandon_after_exit"] = "inspect",
        expected_copy_sha256: str | None = None, expected_native_sha256: str | None = None) -> AdobeRecovery:
    """Inspect retained completion; adopt a saved owned copy with both observed hashes, or abandon after app exit."""
    return invoke(app(application).operation_control, action, session_id, operation_id,
                  expected_copy_sha256, expected_native_sha256)


class BezierPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anchor: Annotated[list[float], Field(min_length=2, max_length=2)]
    left: Annotated[list[float], Field(min_length=2, max_length=2)]
    right: Annotated[list[float], Field(min_length=2, max_length=2)]


class NativeFont(BaseModel):
    name: str
    family: str
    style: str
    postscript_name: str


class AdobeFonts(AdobeSnapshot):
    fonts: list[NativeFont]


@mcp.tool(annotations=READ)
def adobe_fonts(application: App) -> AdobeFonts:
    """List native font families, styles and exact PostScript names."""
    return invoke(app(application).fonts)


@mcp.tool(annotations=MUTATE)
def adobe_add_path(application: App, session_id: str, name: str,
                   points: Annotated[list[BezierPoint], Field(min_length=2, max_length=1024)],
                   closed: bool = True, rgb: list[float] | None = None,
                   group_path: LayerPath | None = None,
                   expected_group_name: str | None = None) -> AdobeMutation:
    """Create retained editable Bézier geometry, optionally filled; coordinates use px/pt from top left."""
    return invoke(app(application).add_path, session_id, name,
                  [p.model_dump() for p in points], closed=closed, rgb=rgb,
                  group_path=group_path, expected_group_name=expected_group_name)


@mcp.tool(annotations=MUTATE)
def adobe_add_text(application: App, session_id: str, name: str, text: str,
                   font: str, position: list[float], size: float, rgb: list[float]) -> AdobeMutation:
    """Create editable native text using an exact discovered PostScript font and point size."""
    return invoke(app(application).add_text, session_id, name, text, font, position, size, rgb)


@mcp.tool(annotations=MUTATE)
def adobe_apply_mask(application: App, session_id: str, layer_path: LayerPath,
                     expected_name: str, path_name: str) -> AdobeMutation:
    """Apply a retained named path as a PS layer mask or Illustrator clipping group."""
    return invoke(app(application).apply_mask, session_id, layer_path, expected_name, path_name)


@mcp.tool(annotations=WRITE)
def media_review_movie(receipt: str, directory: str,
                       codec: Literal["prores_alpha", "h264_black"] = "prores_alpha") -> MovieExport:
    """Run free local FFmpeg/ffprobe on pinned frames; MOV retains alpha, MP4 explicitly composites black."""
    return invoke(native_media.review_movie, workspace(), receipt, directory, codec)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
