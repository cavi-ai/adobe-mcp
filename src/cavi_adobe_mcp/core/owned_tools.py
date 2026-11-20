"""Tool argument and result models shared by the Photoshop and Illustrator servers."""

from typing import Annotated, Literal

from cavi_mcp_core.contracts import Result
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .extendscript import AdobeOutcomeUnknown

Index = Annotated[StrictInt, Field(ge=0, le=10000)]
LayerPath = Annotated[list[Index], Field(min_length=1, max_length=32)]


class LayerEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: LayerPath
    expected_name: str
    name: str | None = None
    visible: bool | None = None
    opacity: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None = None


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


class AdobeSnapshot(Result):
    runtime: dict
    pending_operation: dict | None


class AdobeFonts(AdobeSnapshot):
    fonts: list[NativeFont]


class AdobeMutation(Result):
    session_id: str
    operation_id: str
    document: dict
    copy_: dict = Field(alias="copy")
    source_sha256: str
    retry_safe: Literal[False]


class AdobeExport(AdobeMutation):
    generative_operations: list[dict] | None = None


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


def invoke(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (OSError, ValueError, AdobeOutcomeUnknown) as exc:
        raise ToolError(str(exc)) from exc
