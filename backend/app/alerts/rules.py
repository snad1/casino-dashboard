"""What a rule can watch and the parameters each kind takes, checked before a rule is used.

Profit is the bot's own number for the chosen period: the session, the run (the bot's
run-wide profit) or the host-local day across every lane. Stop reasons are the texts the bot
writes after `STOPPED: ` (`wolfbet.py` `should_stop` and the balance and recovery stops).
"""

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

PATTERN_MAX = 200
FOR_MAX_S = 86400

STOP_REASONS = (
    "Profit target reached",
    "Max loss hit",
    "Max bets reached",
    "Max wins reached",
    "Balance floor hit",
    "Balance goal reached",
    "Loss streak reset",
    "Lane budget exceeded",
    "Insufficient balance",
    "Recovery failed",
)
StopReason = Literal[
    "Profit target reached",
    "Max loss hit",
    "Max bets reached",
    "Max wins reached",
    "Balance floor hit",
    "Balance goal reached",
    "Loss streak reset",
    "Lane budget exceeded",
    "Insufficient balance",
    "Recovery failed",
]


class Kind(StrEnum):
    PROFIT = "profit"
    LOSS = "loss"
    DRAWDOWN = "drawdown"
    DOWN = "down"
    STOP_REASON = "stop_reason"
    BPS_BELOW = "bps_below"
    PUBLISHER_DROPPED = "publisher_dropped"
    INGEST_LAG = "ingest_lag"
    RECONCILE_MISMATCH = "reconcile_mismatch"


INGEST_KINDS = frozenset({Kind.INGEST_LAG, Kind.RECONCILE_MISMATCH})


def _finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("must be a finite number")
    return value


_NESTED = re.compile(r"\([^)]*[+*][^)]*\)\s*[+*{]")


def _pattern(value: str) -> str:
    """A regex without nested repeats, so a crafted pattern can't hang the engine."""
    if _NESTED.search(value):
        raise ValueError("must not repeat a group that itself repeats")
    try:
        re.compile(value)
    except re.error:
        raise ValueError("is not a valid pattern") from None
    return value


Amount = Annotated[float, Field(gt=0, le=1e12), AfterValidator(_finite)]
Seconds = Annotated[int, Field(ge=1, le=FOR_MAX_S)]
Pattern = Annotated[str, Field(min_length=1, max_length=PATTERN_MAX), AfterValidator(_pattern)]


@dataclass(frozen=True)
class Firing:
    """One alert to record: which rule, where, the value that crossed and the message."""

    rule_id: int
    lane: int | None
    kind: str
    value: float | None
    message: str


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProfitParams(Params):
    """`at`: fire once profit reaches it; `step`: fire at every further multiple of it."""

    period: Literal["session", "run", "day"] = "session"
    at: Amount | None = None
    step: Amount | None = None

    @model_validator(mode="after")
    def _one_of(self) -> "ProfitParams":
        if (self.at is None) == (self.step is None):
            raise ValueError("set exactly one of at or step")
        return self


class LossParams(Params):
    """Fire once the period's profit falls to minus `at` or below."""

    period: Literal["session", "run", "day"] = "session"
    at: Amount


class DrawdownParams(Params):
    """Fire once the balance is `pct` percent below its peak this session."""

    pct: Annotated[float, Field(gt=0, le=100), AfterValidator(_finite)]


class DownParams(Params):
    statuses: list[Literal["down", "stale", "events_off"]] = Field(default=["down"], min_length=1)
    for_s: Seconds = 60


class StopReasonParams(Params):
    """Any listed reason, or the reason text matching `pattern`; empty lists mean any stop."""

    reasons: list[StopReason] = Field(default_factory=list)
    pattern: (
        Annotated[str, Field(min_length=1, max_length=PATTERN_MAX), AfterValidator(_pattern)] | None
    ) = None


class BpsBelowParams(Params):
    bps: Annotated[float, Field(gt=0, le=1e6), AfterValidator(_finite)]
    for_s: Seconds = 60


class NoParams(Params):
    pass


class IngestLagParams(Params):
    """Fire when ingest is this far behind; without `seconds`, when reconcile flags its lag."""

    seconds: Seconds | None = None


PARAMS: dict[Kind, type[Params]] = {
    Kind.PROFIT: ProfitParams,
    Kind.LOSS: LossParams,
    Kind.DRAWDOWN: DrawdownParams,
    Kind.DOWN: DownParams,
    Kind.STOP_REASON: StopReasonParams,
    Kind.BPS_BELOW: BpsBelowParams,
    Kind.PUBLISHER_DROPPED: NoParams,
    Kind.INGEST_LAG: IngestLagParams,
    Kind.RECONCILE_MISMATCH: NoParams,
}


class Rule(BaseModel):
    """A rule as the engine uses it: params already checked against its kind."""

    model_config = ConfigDict(frozen=True)

    id: int
    service: str
    lane: int | None
    kind: Kind
    name: str
    params: Params
    cooldown_s: int = Field(ge=0, le=86400)

    @classmethod
    def build(
        cls,
        *,
        id: int,
        service: str,
        lane: int | None,
        kind: str,
        name: str,
        params: dict[str, object],
        cooldown_s: int,
    ) -> "Rule":
        """Raises `ValueError` or `ValidationError` for an unknown kind or bad params."""
        rule_kind = Kind(kind)
        return cls(
            id=id,
            service=service,
            lane=lane,
            kind=rule_kind,
            name=name,
            params=PARAMS[rule_kind].model_validate(params),
            cooldown_s=cooldown_s,
        )
