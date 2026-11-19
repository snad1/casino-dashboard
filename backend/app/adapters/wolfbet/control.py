"""WolfBet's control buttons and their request bodies.

The body models mirror the agent's argument models in `agent/agent/services/wolfbet.py`
field for field, so a bad value is refused here before it reaches the host. Their JSON
Schemas are pinned in `contracts/schemas/agent-wolfbet-args-v1.json`, which both sides
test against.
"""

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.adapters.base import ControlAction
from app.auth.permissions import Perm

LANES_MAX = 10
MAX_CONFIG_PAIRS = 32
_BRANCH = re.compile(r"[A-Za-z0-9._/-]{1,100}")


def _branch(value: str) -> str:
    if (
        not _BRANCH.fullmatch(value)
        or value.startswith(("-", "/", "."))
        or value.endswith(("/", ".", ".lock"))
        or ".." in value
        or "//" in value
        or "/." in value
    ):
        raise ValueError("is not a valid branch name")
    return value


Lane = Annotated[int, Field(ge=1, le=LANES_MAX)]
LaneTarget = Lane | Literal["all"]
Branch = Annotated[str, AfterValidator(_branch)]


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class NoArgs(Args):
    pass


class TargetArgs(Args):
    target: LaneTarget = "all"


class LanesArgs(Args):
    count: Lane
    split: Literal["none", "budget", "bet", "both"] | None = None


class CleanupArgs(Args):
    days: int | None = Field(default=None, ge=1, le=30)


class UpdateArgs(Args):
    branch: Branch | None = None


class ConfigArgs(Args):
    pairs: dict[str, str] = Field(min_length=1, max_length=MAX_CONFIG_PAIRS)


_BASIC = Perm.CONTROL_BASIC
_CONFIG = Perm.CONFIG_WRITE
_DESTRUCTIVE = Perm.CONTROL_DESTRUCTIVE

ACTIONS: dict[str, ControlAction] = {
    "start": ControlAction(_BASIC, "start", NoArgs),
    "stop": ControlAction(_BASIC, "stop", NoArgs),
    "pause": ControlAction(_BASIC, "pause", TargetArgs),
    "resume": ControlAction(_BASIC, "resume", TargetArgs),
    "reload": ControlAction(_BASIC, "reload", TargetArgs),
    "restart": ControlAction(_BASIC, "restart", NoArgs, job=True),
    "config_set": ControlAction(_CONFIG, "config_set", ConfigArgs, fresh=True),
    "lanes": ControlAction(_CONFIG, "lanes", LanesArgs, fresh=True, job=True),
    "reset": ControlAction(_DESTRUCTIVE, "reset", NoArgs, fresh=True),
    "run_reset": ControlAction(_DESTRUCTIVE, "run_reset", NoArgs, fresh=True),
    "stop_all": ControlAction(_DESTRUCTIVE, "stop_all", NoArgs, fresh=True),
    "cleanup": ControlAction(_DESTRUCTIVE, "cleanup", CleanupArgs, fresh=True),
    "update": ControlAction(_DESTRUCTIVE, "update", UpdateArgs, fresh=True, job=True),
    "doctor_fix": ControlAction(
        _DESTRUCTIVE, "doctor", NoArgs, fresh=True, job=True, fixed_args={"fix": True}
    ),
}
