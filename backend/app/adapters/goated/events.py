"""Goated daemon events, version 1 (`contracts/schemas/goated-events-v1.json`).

The envelope and the lifecycle payload are WolfBet's. A bet names the game, the payout target
the bot aimed for and the casino's own text for the result (`2.31x` on limbo, empty on dice);
goated has no dice rule or bet value. The bet payload is closed, the state payload an open
mapping that mirrors the bot's state file.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.adapters.base import Event
from app.adapters.wolfbet.events import LifecyclePayload

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class GoatedBetPayload(BaseModel):
    model_config = _STRICT

    session_id: int
    game: Literal["limbo", "dice"]
    amount: float
    multiplier_target: float
    result_value: float
    result_display: str
    state: Literal["win", "loss"]
    profit: float
    balance_after: float
    session_profit: float
    total_bets: Annotated[int, Field(ge=0)]
    current_streak: int
    api_ms: float

    @property
    def multiplier(self) -> float:
        """The payout the bet aimed for, under the name the live model reads for every bot."""
        return self.multiplier_target


class _GoatedEvent(Event):
    model_config = _STRICT

    v: Literal[1]
    svc: Literal["goated"]
    lane: Annotated[int, Field(ge=1)]
    pid: Annotated[int, Field(ge=1)]
    seq: Annotated[int, Field(ge=0)]
    ts_ms: Annotated[int, Field(ge=0)]


class GoatedBetEvent(_GoatedEvent):
    type: Literal["bet"]
    payload: GoatedBetPayload


class GoatedStateEvent(_GoatedEvent):
    type: Literal["state"]
    payload: dict[str, Any]


class GoatedLifecycleEvent(_GoatedEvent):
    type: Literal["lifecycle"]
    payload: LifecyclePayload


GoatedEvent = Annotated[
    GoatedBetEvent | GoatedStateEvent | GoatedLifecycleEvent, Field(discriminator="type")
]
