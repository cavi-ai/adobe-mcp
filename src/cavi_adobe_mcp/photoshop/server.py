"""Explicit native Photoshop authoring on owned PSD copies; lazy app access."""

from typing import Annotated, Literal

from cavi_mcp_core.contracts import Result
from cavi_mcp_core.server import DESTRUCTIVE as MUTATE
from cavi_mcp_core.server import READ_ONLY as READ
from cavi_mcp_core.server import WRITES as WRITE
from cavi_mcp_core.server import Server
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from ..core.config import workspace
from ..core.owned_tools import (
    AdobeExport,
    AdobeMutation,
    AdobeRecovery,
    AdobeSnapshot,
    BezierPoint,
    LayerEdit,
    LayerPath,
    NativeFont,
    invoke,
)
from . import bridge

mcp = Server("cavi-adobe-photoshop", package="cavi-adobe-mcp", anticipated=(), instructions=(
    "Explicit local Photoshop edits on new owned PSD copies. Server startup never launches Photoshop. "
    "Start with photoshop_status; native document operations require an already running app and "
    "local Automation access. Never sign in or retry an unknown outcome. Keep .adobe-authoring "
    "state private. The generative tools use the signed-in Creative Cloud account and spend "
    "generative credits."
))
CLOUD = MUTATE.model_copy(update={"open_world_hint": True})


class PhotoshopStatus(Result):
    app: Literal["photoshop"]
    application: str
    version: str
    running: bool
    launches_application: Literal[False]
    authenticates: Literal[False]


class PhotoshopFont(NativeFont):
    source: Literal["adobe_fonts", "system", "local", "application", "unknown"]
    file: str | None


class FontList(BaseModel):
    path: str
    modified: str


class PhotoshopFonts(AdobeSnapshot):
    fonts: list[PhotoshopFont]
    font_list: FontList | None


class TextRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_: StrictInt = Field(alias="from")
    to: StrictInt
    postscript_name: str | None
    available: bool


class TextFontLayer(BaseModel):
    path: list[int]
    name: str
    native_id: int | None
    runs: list[TextRun]


class PhotoshopTextFonts(AdobeSnapshot):
    layers: list[TextFontLayer]
    missing_fonts: list[str]


class PhotoshopCanvas(AdobeMutation):
    source_kind: Literal["canvas_recipe"]
    source_sha256_kind: Literal["canvas_recipe_json"]
    canvas_recipe: dict


class PhotoshopGenerative(AdobeMutation):
    generative: dict | None = None


def host(timeout=45):
    return bridge.PhotoshopAuthoring(workspace(), timeout=timeout)


@mcp.tool(annotations=READ)
def photoshop_status() -> PhotoshopStatus:
    """Inspect installation and running process only; no app launch or Apple events."""
    return invoke(host().status)


@mcp.tool(annotations=READ)
def photoshop_inspect(session_id: str | None = None) -> AdobeSnapshot:
    """Read native document/layer identities; an unknown command blocks later mutations."""
    owned = host()
    if session_id is None:
        return invoke(owned.inspect)
    result = invoke(owned.inspect_session, session_id)
    return {**result["native"], "session": result["session"]}


@mcp.tool(annotations=READ)
def photoshop_fonts(family: Annotated[str, Field(min_length=1, max_length=128)] | None = None) -> PhotoshopFonts:
    """List installed fonts with family, style, exact PostScript name, source and font file path. source is
    adobe_fonts (activated in Creative Cloud), system, local, application or unknown; adobe_fonts is detected from
    Photoshop's font list and is unknown when the list has no record. family keeps one family (exact, case-insensitive).
    Fonts are activated in Creative Cloud, never by this server."""
    return invoke(host().fonts, family)


@mcp.tool(annotations=READ)
def photoshop_text_fonts(session_id: str) -> PhotoshopTextFonts:
    """Read every text layer of an owned copy: its path, name, native_id and style runs (from/to character
    offsets, PostScript name, available). missing_fonts lists the PostScript names Photoshop cannot use."""
    return invoke(host().text_fonts, session_id)


@mcp.tool(annotations=WRITE)
def photoshop_open_copy(source: str, destination: str) -> AdobeMutation:
    """Open a new owned PSD copy from a workspace PSD or PNG. Destination must be new."""
    return invoke(host().open_copy, source, destination)


@mcp.tool(annotations=WRITE)
def photoshop_create_canvas(width: Annotated[StrictInt, Field(ge=1, le=2880)],
                            height: Annotated[StrictInt, Field(ge=1, le=2880)],
                            destination: str, bit_depth: Literal[8, 16] = 16) -> PhotoshopCanvas:
    """Create a transparent 72 ppi sRGB Photoshop canvas; default depth is 16-bit."""
    return invoke(host().create_canvas, width, height, destination, bit_depth)


@mcp.tool(annotations=MUTATE)
def photoshop_resize_image(session_id: str, width: Annotated[StrictInt, Field(ge=1, le=2880)],
                           height: Annotated[StrictInt, Field(ge=1, le=2880)]) -> AdobeMutation:
    """Resample an owned PSD to pixel dimensions while retaining editable layers and paths."""
    return invoke(host().resize_image, session_id, width, height)


@mcp.tool(annotations=MUTATE)
def photoshop_edit_layers(session_id: str,
                          edits: Annotated[list[LayerEdit], Field(min_length=1, max_length=64)]) -> AdobeMutation:
    """Rename, hide/show or change opacity of exact paths with expected names in an owned copy."""
    return invoke(host().edit_layers, session_id,
                  [item.model_dump(exclude_none=True) for item in edits])


@mcp.tool(annotations=MUTATE)
def photoshop_add_group(session_id: str, name: str, parent_path: LayerPath | None = None,
                        expected_parent_name: str | None = None) -> AdobeMutation:
    """Create a named layer group for editable motifs or layered cartoon source parts."""
    return invoke(host().add_group, session_id, name, parent_path=parent_path,
                  expected_parent_name=expected_parent_name)


@mcp.tool(annotations=MUTATE)
def photoshop_place_image(session_id: str, source: str, name: str,
                          position: Annotated[list[float], Field(min_length=2, max_length=2)],
                          group_path: LayerPath | None = None,
                          expected_group_name: str | None = None) -> AdobeMutation:
    """Embed an existing PNG on a new layer in a named group; x/y are pixels from the top left."""
    return invoke(host().place_image, session_id, source, name, position,
                  group_path=group_path, expected_group_name=expected_group_name)


@mcp.tool(annotations=MUTATE)
def photoshop_add_rectangle(session_id: str, name: str,
                            bounds: Annotated[list[float], Field(min_length=4, max_length=4)],
                            rgb: Annotated[list[float], Field(min_length=3, max_length=3)],
                            opacity: Annotated[float, Field(ge=0, le=100)] = 100) -> AdobeMutation:
    """Add a filled motif or mask on a new pixel layer; bounds are x,y,width,height in px."""
    return invoke(host().add_rectangle, session_id, name, bounds, rgb, opacity)


@mcp.tool(annotations=MUTATE)
def photoshop_add_path(session_id: str, name: str,
                       points: Annotated[list[BezierPoint], Field(min_length=2, max_length=1024)],
                       closed: bool = True, rgb: list[float] | None = None,
                       group_path: LayerPath | None = None,
                       expected_group_name: str | None = None) -> AdobeMutation:
    """Create a retained document path from Bézier points, optionally filled on a new layer; px from the top left."""
    return invoke(host().add_path, session_id, name,
                  [p.model_dump() for p in points], closed=closed, rgb=rgb,
                  group_path=group_path, expected_group_name=expected_group_name)


@mcp.tool(annotations=MUTATE)
def photoshop_add_text(session_id: str, name: str, text: str, font: str,
                       position: list[float], size: float, rgb: list[float]) -> AdobeMutation:
    """Create an editable text layer using an exact PostScript font from photoshop_fonts; size in points."""
    return invoke(host().add_text, session_id, name, text, font, position, size, rgb)


@mcp.tool(annotations=MUTATE)
def photoshop_set_text_font(session_id: str, layer_path: LayerPath, expected_name: str,
                            font: str) -> AdobeMutation:
    """Set every run of one exact unlocked text layer to an exact PostScript font from photoshop_fonts. Refused
    when Photoshop cannot use the font or any run keeps another font. Fonts are activated in Creative Cloud."""
    return invoke(host().set_text_font, session_id, layer_path, expected_name, font)


@mcp.tool(annotations=MUTATE)
def photoshop_apply_mask(session_id: str, layer_path: LayerPath, expected_name: str,
                         path_name: str) -> AdobeMutation:
    """Apply one retained closed named path as a layer mask on an exact unmasked layer."""
    return invoke(host().apply_mask, session_id, layer_path, expected_name, path_name)


@mcp.tool(annotations=CLOUD)
def photoshop_generative_fill(session_id: str, prompt: Annotated[str, Field(max_length=1000)],
                              bounds: Annotated[list[float], Field(min_length=4, max_length=4)] | None = None) -> PhotoshopGenerative:
    """Generate a new layer from a prompt in x,y,width,height px, or the whole canvas. Sends pixels to Adobe and
    spends generative credits; Photoshop's one-time Generative Credits notice must already be acknowledged."""
    return invoke(host(120).generative_fill, session_id, prompt, bounds)


@mcp.tool(annotations=MUTATE)
def photoshop_remove_background(session_id: str, layer_path: LayerPath,
                                expected_name: str) -> AdobeMutation:
    """Add a layer mask that keeps the detected subject of one exact unmasked pixel layer."""
    return invoke(host(120).remove_background, session_id, layer_path, expected_name)


@mcp.tool(annotations=CLOUD)
def photoshop_generative_upscale(session_id: str) -> PhotoshopGenerative:
    """Double an owned PSD of at most 1440 px per side and add the upscaled image as a new top layer.
    Sends pixels to Adobe and may spend generative credits."""
    return invoke(host(120).generative_upscale, session_id)


@mcp.tool(annotations=WRITE)
def photoshop_export(session_id: str, destination: str,
                     usage: Literal["color", "data"] = "color") -> AdobeExport:
    """Export a PNG at the document's native bit depth; color is converted to sRGB, data is untagged."""
    return invoke(host().export, session_id, destination, usage=usage)


@mcp.tool(annotations=MUTATE)
def photoshop_close_copy(session_id: str) -> AdobeMutation:
    """Close only the exact cooperatively owned copy without saving; every earlier operation already saved it."""
    return invoke(host().close_copy, session_id)


@mcp.tool(annotations=MUTATE)
def photoshop_operation_control(session_id: str, operation_id: str,
        action: Literal["inspect", "adopt_current", "abandon_after_exit"] = "inspect",
        expected_copy_sha256: str | None = None, expected_native_sha256: str | None = None) -> AdobeRecovery:
    """Inspect retained completion; adopt a saved owned copy with both observed hashes, or abandon after app exit."""
    return invoke(host().operation_control, action, session_id, operation_id,
                  expected_copy_sha256, expected_native_sha256)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
