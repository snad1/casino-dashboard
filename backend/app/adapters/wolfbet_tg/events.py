"""Telegram bot events, version 1 (`contracts/schemas/wolfbet-tg-events-v1.json`).

The daemon's envelope and payloads, published per Telegram user: `svc` is `wolfbet-tg`, every
payload names its `uid`, and `seq` counts per uid, so `scope` is that uid. The models extend the
daemon's, so the live model treats a user's events exactly like the daemon's.
"""

from typing import Annotated, Any, Literal

from pydantic import Field, field_validator

from app.adapters.wolfbet.events import (
    BetEvent,
    BetPayload,
    LifecycleEvent,
    LifecyclePayload,
    StateEvent,
)

Uid = Annotated[int, Field(ge=1, le=2**63 - 1)]


class TgBetPayload(BetPayload):
    uid: Uid


class TgLifecyclePayload(LifecyclePayload):
    uid: Uid


class TgBetEvent(BetEvent):
    svc: Literal["wolfbet-tg"]  # type: ignore[assignment]  # narrows the daemon's service
    lane: Literal[1]
    payload: TgBetPayload

    @property
    def scope(self) -> int:
        return self.payload.uid


class TgStateEvent(StateEvent):
    svc: Literal["wolfbet-tg"]  # type: ignore[assignment]  # narrows the daemon's service
    lane: Literal[1]

    @field_validator("payload")
    @classmethod
    def _names_its_user(cls, payload: dict[str, Any]) -> dict[str, Any]:
        uid = payload.get("uid")
        if type(uid) is not int or not 1 <= uid < 2**63:
            raise ValueError("the state payload must name its user")
        return payload

    @property
    def scope(self) -> int:
        uid: int = self.payload["uid"]
        return uid


class TgLifecycleEvent(LifecycleEvent):
    svc: Literal["wolfbet-tg"]  # type: ignore[assignment]  # narrows the daemon's service
    lane: Literal[1]
    payload: TgLifecyclePayload

    @property
    def scope(self) -> int:
        return self.payload.uid


WolfBetTgEvent = Annotated[
    TgBetEvent | TgStateEvent | TgLifecycleEvent, Field(discriminator="type")
]
