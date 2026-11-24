"""What the live pipeline and the control API need from each bot service."""

from collections.abc import Callable, Mapping
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

    @property
    def scope(self) -> int | None:
        """Whose numbering `seq` follows inside one process: None when the process has one
        counter, a user id when a bot counts per user."""
        return None


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
    `job.start` and answers with a job id. `agent_args` turns the body into the agent's
    arguments when they differ (default: the body's fields); `fixed_args` are merged over.
    `label`, `title`, `effect` and `confirm` are the button and confirm-modal text; the
    last three may name `{service}` or a body field such as `{target}`. A read with
    `confirm_first` False runs on the press, without a modal. `exclusive` jobs run one at a
    time per service on the host (the agent refuses a second), so while one runs the
    other control buttons wait; reads such as a log follow are not exclusive. `lane_field`
    names the body field holding the one lane the action touches, so a role on that lane
    alone may run it.
    """

    perm: Perm
    cmd: str
    body: type[BaseModel]
    label: str
    title: str
    effect: str
    confirm: str
    fresh: bool = False
    confirm_first: bool = True
    exclusive: bool = True
    job: bool = False
    agent_args: Callable[[Any], dict[str, Any]] | None = None
    fixed_args: Mapping[str, Any] = field(default_factory=dict)
    lane_field: str | None = None
