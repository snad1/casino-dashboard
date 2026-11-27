"""The goated bot: its event streams and how to read them."""

from pydantic import TypeAdapter

from app.adapters.goated.events import (
    GoatedBetEvent,
    GoatedEvent,
    GoatedLifecycleEvent,
    GoatedStateEvent,
)
from app.adapters.wolfbet import parse_v1

_EVENT: TypeAdapter[GoatedBetEvent | GoatedStateEvent | GoatedLifecycleEvent] = TypeAdapter(
    GoatedEvent
)


class GoatedAdapter:
    name: str = "goated"
    streams: tuple[str, ...] = ("goated:bets", "goated:state", "goated:lifecycle")

    def parse(self, raw: bytes) -> GoatedBetEvent | GoatedStateEvent | GoatedLifecycleEvent:
        """Validate one `data` field; reasons are `json`, `version` and `shape`."""
        return parse_v1(raw, _EVENT)
