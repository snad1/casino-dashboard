"""The WolfBet Telegram bot: one set of event streams per Telegram user."""

from collections.abc import Iterable

from pydantic import TypeAdapter

from app.adapters.wolfbet import parse_v1
from app.adapters.wolfbet_tg.events import (
    TgBetEvent,
    TgLifecycleEvent,
    TgStateEvent,
    WolfBetTgEvent,
)

KINDS = ("bets", "state", "lifecycle")

_EVENT: TypeAdapter[TgBetEvent | TgStateEvent | TgLifecycleEvent] = TypeAdapter(WolfBetTgEvent)


class WolfBetTgAdapter:
    """The daemon's event format per user; which streams exist depends on which users do."""

    name: str = "wolfbet-tg"
    streams: tuple[str, ...] = ()

    def parse(self, raw: bytes) -> TgBetEvent | TgStateEvent | TgLifecycleEvent:
        """Validate one `data` field; reasons are `json`, `version` and `shape`."""
        return parse_v1(raw, _EVENT)

    def streams_for(self, uids: Iterable[int]) -> tuple[str, ...]:
        """The three streams of each user, in a stable order."""
        return tuple(f"{self.name}:{uid}:{kind}" for uid in sorted(set(uids)) for kind in KINDS)
