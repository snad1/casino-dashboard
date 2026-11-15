"""Wire format between the api and the agent (JSON lines over a unix socket)."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PROTO_VERSION = 1


class Request(BaseModel):
    """One request from the api; unknown fields and loose types are refused."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    proto: Literal[1]
    id: str = Field(min_length=1, max_length=64)
    cmd: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.]*$")
    args: dict[str, Any] = Field(default_factory=dict)
