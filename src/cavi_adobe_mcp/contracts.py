"""Base model for the Adobe tool results."""

from pydantic import BaseModel, ConfigDict


class Result(BaseModel):
    """A tool result; fields beyond the declared ones are kept."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
