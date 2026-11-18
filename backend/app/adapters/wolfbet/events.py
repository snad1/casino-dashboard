"""WolfBet daemon events, version 1 (`contracts/schemas/wolfbet-events-v1.json`).

Bet and lifecycle payloads are closed: a field the contract does not name is refused. The
state payload is an open object in the contract, since it mirrors the bot's state file and
grows with it, so it stays a plain mapping here.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.adapters.base import Event

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class _Payload(BaseModel):
    model_config = _STRICT


class BetPayload(_Payload):
    session_id: int
    amount: float
    multiplier: float
    rule: str
    bet_value: float
    result_value: float
    state: Literal["win", "loss"]
    profit: float
    balance_after: float
    session_profit: float
    total_bets: Annotated[int, Field(ge=0)]
    current_streak: int
    api_ms: float


class LifecyclePayload(_Payload):
    kind: Literal[
        "start",
        "stop",
        "pause",
        "resume",
        "reload",
        "cycle",
        "session_open",
        "session_close",
        "error",
    ]
    data: dict[str, Any]


class _WolfBetEvent(Event):
    model_config = _STRICT

    v: Literal[1]
    svc: Literal["wolfbet"]
    lane: Annotated[int, Field(ge=1)]
    pid: Annotated[int, Field(ge=1)]
    seq: Annotated[int, Field(ge=0)]
    ts_ms: Annotated[int, Field(ge=0)]


class BetEvent(_WolfBetEvent):
    type: Literal["bet"]
    payload: BetPayload


class StateEvent(_WolfBetEvent):
    type: Literal["state"]
    payload: dict[str, Any]


class LifecycleEvent(_WolfBetEvent):
    type: Literal["lifecycle"]
    payload: LifecyclePayload


WolfBetEvent = Annotated[BetEvent | StateEvent | LifecycleEvent, Field(discriminator="type")]
