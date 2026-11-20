"""Data-only native Animate timeline operations. Frame indices are zero based."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Frame = Annotated[int, Field(strict=True, ge=0, le=999)]
Name = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f]+$")]
Number = Annotated[float, Field(allow_inf_nan=False, ge=-100000, le=100000)]


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    layer: Name


class AddLayer(Edit):
    action: Literal["add_layer"]


class Duration(Edit):
    action: Literal["duration"]
    count: Annotated[int, Field(strict=True, ge=1, le=1000)]


class Keyframe(Edit):
    action: Literal["keyframe"]
    frame: Frame
    blank: bool = False


class Transform(Edit):
    action: Literal["transform"]
    frame: Frame
    element: Annotated[int, Field(strict=True, ge=0, le=10000)]
    matrix: Annotated[list[Number], Field(min_length=6, max_length=6)]


class Symbol(Edit):
    action: Literal["symbol"]
    frame: Frame
    element: Annotated[int, Field(strict=True, ge=0, le=10000)]
    name: Name
    symbol_type: Literal["graphic", "movie clip", "button"] = "graphic"


class PlaceSymbol(Edit):
    action: Literal["place_symbol"]
    frame: Frame
    name: Name
    x: Number
    y: Number


class Tween(Edit):
    action: Literal["tween"]
    frame: Frame
    end: Frame
    easing: Annotated[int, Field(strict=True, ge=-100, le=100)] = 0

    @model_validator(mode="after")
    def order(self):
        if self.end <= self.frame:
            raise ValueError("Tween end must follow its start")
        return self


TimelineEdit = Annotated[AddLayer | Duration | Keyframe | Transform | Symbol | PlaceSymbol | Tween,
                         Field(discriminator="action")]


class TimelineBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edits: Annotated[list[TimelineEdit], Field(min_length=1, max_length=128)]
