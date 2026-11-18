"""What the live pipeline needs from each bot service it watches."""

from typing import Protocol

from pydantic import BaseModel


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
