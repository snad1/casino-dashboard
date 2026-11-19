"""What the live pipeline and the control API need from each bot service."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

from app.auth.permissions import Perm


class InvalidEvent(Exception):
    """A stream entry that does not match the contract; `reason` is a short counter key."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Event(BaseModel):
    """Fields every service's events share, enough to order them and detect loss."""

    lane: int
    pid: int
    seq: int
    ts_ms: int


class ServiceAdapter(Protocol):
    name: str
    streams: tuple[str, ...]

    def parse(self, raw: bytes) -> Event:
        """The typed event in one stream entry's `data` field, or `InvalidEvent`."""
        ...


@dataclass(frozen=True)
class ControlAction:
    """One control button: who may press it, what it sends to the host agent and how.

    `cmd` is the agent command without the service prefix; `job` runs it through
    `job.start` and answers with a job id. `fixed_args` are merged over the body.
    `label`, `title`, `effect` and `confirm` are the button and confirm-modal text; the
    last three may name `{service}` or a body field such as `{target}`.
    """

    perm: Perm
    cmd: str
    body: type[BaseModel]
    label: str
    title: str
    effect: str
    confirm: str
    fresh: bool = False
    job: bool = False
    fixed_args: Mapping[str, Any] = field(default_factory=dict)
