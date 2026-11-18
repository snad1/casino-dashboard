"""The WolfBet bot: its event streams and how to read them."""

import json

from pydantic import TypeAdapter, ValidationError

from app.adapters.base import InvalidEvent
from app.adapters.wolfbet.events import BetEvent, LifecycleEvent, StateEvent, WolfBetEvent

SCHEMA_VERSION = 1

_EVENT: TypeAdapter[BetEvent | StateEvent | LifecycleEvent] = TypeAdapter(WolfBetEvent)


class WolfBetAdapter:
    name: str = "wolfbet"
    streams: tuple[str, ...] = ("wolfbet:bets", "wolfbet:state", "wolfbet:lifecycle")

    def parse(self, raw: bytes) -> BetEvent | StateEvent | LifecycleEvent:
        """Validate one `data` field; reasons are `json`, `version` and `shape`."""
        try:
            document = json.loads(raw)
        except ValueError:
            raise InvalidEvent("json") from None
        if not isinstance(document, dict):
            raise InvalidEvent("shape")
        if document.get("v") != SCHEMA_VERSION:
            raise InvalidEvent("version")
        try:
            return _EVENT.validate_python(document)
        except ValidationError:
            raise InvalidEvent("shape") from None
