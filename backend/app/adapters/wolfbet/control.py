"""WolfBet's control buttons and their request bodies.

The body models mirror the agent's argument models in `agent/agent/services/wolfbet.py`
field for field, so a bad value is refused here before it reaches the host. Their JSON
Schemas are pinned in `contracts/schemas/agent-wolfbet-args-v1.json`, which both sides
test against.
"""

import math
import re
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.adapters.base import ControlAction
from app.auth.permissions import Perm

LANES_MAX = 10
MAX_CONFIG_PAIRS = 32
_BRANCH = re.compile(r"[A-Za-z0-9._/-]{1,100}")
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")


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


def _name(value: str) -> str:
    if not _NAME.fullmatch(value) or value.startswith(("-", ".")):
        raise ValueError("must be 1-64 letters, digits, '_', '.' or '-', not starting with - or .")
    return value


def _finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("must be a finite number")
    return value


Lane = Annotated[int, Field(ge=1, le=LANES_MAX)]
LaneTarget = Lane | Literal["all"]
Branch = Annotated[str, AfterValidator(_branch)]
Name = Annotated[str, AfterValidator(_name)]
PositiveFloat = Annotated[float, Field(gt=0, le=1e12), AfterValidator(_finite)]


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


class LogsArgs(Args):
    service: Literal["cli", "tg", "web"] = "cli"
    lines: int = Field(default=200, ge=1, le=2000)


class FollowArgs(Args):
    service: Literal["cli", "tg", "web"] = "cli"


class BenchmarkArgs(Args):
    count: int = Field(default=20, ge=3, le=200)
    lanes: Lane | None = None


class BacktestArgs(Args):
    simulate: int | None = Field(default=None, ge=1, le=5_000_000)
    replay_session: int | None = Field(default=None, ge=1)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)
    sessions: int | None = Field(default=None, ge=1, le=1_000_000)
    runs: int | None = Field(default=None, ge=1, le=100_000)
    balance: PositiveFloat | None = None
    preset: Name | None = None

    @model_validator(mode="after")
    def _one_source(self) -> "BacktestArgs":
        if self.simulate is not None and self.replay_session is not None:
            raise ValueError("simulate and replay_session are exclusive")
        return self


class ExportArgs(Args):
    session_id: int | None = Field(default=None, ge=1)
    format: Literal["csv", "json"] = "csv"

    @model_validator(mode="after")
    def _json_needs_a_session(self) -> "ExportArgs":
        if self.format == "json" and self.session_id is None:
            raise ValueError("a JSON export needs session_id")
        return self


class ConfigArgs(Args):
    pairs: dict[str, str] = Field(min_length=1, max_length=MAX_CONFIG_PAIRS)


class ConfigChange(Args):
    """One config edit: `lane` set overrides that lane, and the value `default` drops it."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    value: str = Field(max_length=200, pattern=r"^[^\x00-\x1f\x7f]*$")
    lane: int | None = Field(default=None, ge=2, le=LANES_MAX)


class ConfigChanges(Args):
    changes: list[ConfigChange] = Field(min_length=1, max_length=MAX_CONFIG_PAIRS)

    @model_validator(mode="after")
    def _each_key_once(self) -> "ConfigChanges":
        targets = [(c.key, c.lane) for c in self.changes]
        if len(set(targets)) != len(targets):
            raise ValueError("each key may change once per lane")
        return self


def config_pairs(body: ConfigChanges) -> dict[str, Any]:
    """The agent's `pairs`, built here so a client never writes `key=value` text itself."""
    pairs = {
        (f"lane{c.lane}.{c.key}" if c.lane is not None else c.key): c.value for c in body.changes
    }
    return ConfigArgs(pairs=pairs).model_dump()


_BASIC = Perm.CONTROL_BASIC
_CONFIG = Perm.CONFIG_WRITE
_DESTRUCTIVE = Perm.CONTROL_DESTRUCTIVE
_LOGS = Perm.LOGS
_EXPORT = Perm.EXPORT

ACTIONS: dict[str, ControlAction] = {
    "start": ControlAction(
        _BASIC,
        "start",
        NoArgs,
        label="Start",
        title="Start {service}?",
        effect="The bot starts betting with its saved config.",
        confirm="Start",
    ),
    "stop": ControlAction(
        _BASIC,
        "stop",
        NoArgs,
        label="Stop",
        title="Stop {service}?",
        effect="All lanes stop after their current bet. Open sessions are saved.",
        confirm="Stop",
    ),
    "pause": ControlAction(
        _BASIC,
        "pause",
        TargetArgs,
        label="Pause",
        title="Pause {target}?",
        effect="Betting stops on {target} until you resume it. The session stays open.",
        confirm="Pause {target}",
    ),
    "resume": ControlAction(
        _BASIC,
        "resume",
        TargetArgs,
        label="Resume",
        title="Resume {target}?",
        effect="Betting starts again on {target} where it paused.",
        confirm="Resume {target}",
    ),
    "reload": ControlAction(
        _BASIC,
        "reload",
        TargetArgs,
        label="Reload",
        title="Reload the config on {target}?",
        effect="The config file is read again on {target} without stopping. The session goes on.",
        confirm="Reload {target}",
    ),
    "restart": ControlAction(
        _BASIC,
        "restart",
        NoArgs,
        label="Restart",
        title="Restart {service}?",
        effect="Lanes stop and start again. The live view shows them as down for a few seconds.",
        confirm="Restart",
        job=True,
    ),
    "config_set": ControlAction(
        _CONFIG,
        "config_set",
        ConfigChanges,
        label="Save config",
        title="Save config changes for {service}?",
        effect="The bot checks the new values first, then uses them from the next bet.",
        confirm="Save config",
        fresh=True,
        agent_args=config_pairs,
    ),
    "lanes": ControlAction(
        _CONFIG,
        "lanes",
        LanesArgs,
        label="Apply",
        title="Change lanes to {count}?",
        effect="The bot restarts with {count} lanes and the {split} split. Lanes above {count} "
        "stop and their sessions end.",
        confirm="Change lanes",
        fresh=True,
        job=True,
    ),
    "reset": ControlAction(
        _DESTRUCTIVE,
        "reset",
        NoArgs,
        label="Reset statistics",
        title="Reset statistics for {service}?",
        effect="The current database is archived and a fresh one starts. Sessions stay in the "
        "archive. The config is not changed.",
        confirm="Reset statistics",
        fresh=True,
    ),
    "run_reset": ControlAction(
        _DESTRUCTIVE,
        "run_reset",
        NoArgs,
        label="Run reset",
        title="Start a new run total for {service}?",
        effect="The run profit total starts again from zero. Sessions and bets are kept.",
        confirm="Start new run",
        fresh=True,
    ),
    "stop_all": ControlAction(
        _DESTRUCTIVE,
        "stop_all",
        NoArgs,
        label="Stop all",
        title="Stop every {service} bot?",
        effect="The betting bot and the Telegram bot both stop. Nothing starts again until "
        "someone starts it.",
        confirm="Stop all",
        fresh=True,
    ),
    "cleanup": ControlAction(
        _DESTRUCTIVE,
        "cleanup",
        CleanupArgs,
        label="Clean up",
        title="Delete bet rows older than {days} days?",
        effect="Old bet rows are removed from the bot's database for good. Session totals "
        "are kept.",
        confirm="Delete old bets",
        fresh=True,
    ),
    "update": ControlAction(
        _DESTRUCTIVE,
        "update",
        UpdateArgs,
        label="Update",
        title="Update {service}?",
        effect="The bot pulls the latest code{branch}, reinstalls and restarts. Lanes are "
        "down while it runs.",
        confirm="Update",
        fresh=True,
        job=True,
    ),
    "doctor_fix": ControlAction(
        _DESTRUCTIVE,
        "doctor",
        NoArgs,
        label="Doctor fix",
        title="Run doctor and apply its fixes on {service}?",
        effect="Doctor checks the host and applies the safe fixes: old bet cleanup, database "
        "VACUUM and journal vacuum.",
        confirm="Run doctor fix",
        fresh=True,
        job=True,
        fixed_args={"fix": True},
    ),
    "logs": ControlAction(
        _LOGS,
        "logs",
        LogsArgs,
        label="Load",
        title="Load the {service} logs?",
        effect="Shows the newest log lines.",
        confirm="Load",
        confirm_first=False,
        exclusive=False,
    ),
    "logs_follow": ControlAction(
        _LOGS,
        "logs_follow",
        FollowArgs,
        label="Follow",
        title="Follow the {service} logs?",
        effect="New log lines stream in until you stop.",
        confirm="Follow",
        job=True,
        confirm_first=False,
        exclusive=False,
    ),
    "diagnose": ControlAction(
        _BASIC,
        "diagnose",
        NoArgs,
        label="Diagnose",
        title="Diagnose {service}?",
        effect="Reads the running session and names what limits its speed. Places no bets.",
        confirm="Diagnose",
        confirm_first=False,
        exclusive=False,
    ),
    "doctor": ControlAction(
        _BASIC,
        "doctor",
        NoArgs,
        label="Doctor",
        title="Run doctor on {service}?",
        effect="Checks the host and the bot and lists fixes. Changes nothing.",
        confirm="Doctor",
        job=True,
        confirm_first=False,
    ),
    "benchmark": ControlAction(
        _BASIC,
        "benchmark",
        BenchmarkArgs,
        label="Benchmark",
        title="Benchmark the {service} api?",
        effect="Times {count} api calls. Places no bets.",
        confirm="Benchmark",
        job=True,
        confirm_first=False,
        exclusive=False,
    ),
    "backtest": ControlAction(
        _EXPORT,
        "backtest",
        BacktestArgs,
        label="Run backtest",
        title="Run a backtest?",
        effect="Simulates the strategy offline. No api calls and no real balance.",
        confirm="Run backtest",
        job=True,
        confirm_first=False,
        exclusive=False,
    ),
    "export": ControlAction(
        _EXPORT,
        "export",
        ExportArgs,
        label="Export",
        title="Export sessions?",
        effect="Writes the sessions to a file you can download for an hour.",
        confirm="Export",
        job=True,
        confirm_first=False,
        exclusive=False,
    ),
}
