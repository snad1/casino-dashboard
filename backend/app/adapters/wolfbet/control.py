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

LANE_ID_MAX = 99
MAX_CONFIG_PAIRS = 32
_BRANCH = re.compile(r"[A-Za-z0-9._/-]{1,100}")
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_LANE_NAME = re.compile(r"[A-Za-z0-9_]([A-Za-z0-9 _-]{0,30}[A-Za-z0-9_-])?")


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


def _lane_name(value: str) -> str:
    if not _LANE_NAME.fullmatch(value):
        raise ValueError(
            "must be 1-32 letters, digits, space, '_' or '-', not starting with - or a space"
        )
    return value


def _finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("must be a finite number")
    return value


Lane = Annotated[int, Field(ge=1, le=LANE_ID_MAX)]
LaneTarget = Lane | Literal["all"]
Branch = Annotated[str, AfterValidator(_branch)]
Name = Annotated[str, AfterValidator(_name)]
LaneName = Annotated[str, AfterValidator(_lane_name)]
PositiveFloat = Annotated[float, Field(gt=0, le=1e12), AfterValidator(_finite)]


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class NoArgs(Args):
    pass


class TargetArgs(Args):
    target: LaneTarget = "all"


class LaneIdArgs(Args):
    lane: Lane


class RemovableLaneArgs(Args):
    """Any lane but lane 1, which holds the base config."""

    lane: int = Field(ge=2, le=LANE_ID_MAX)


class LaneAddArgs(Args):
    name: LaneName
    copy_from: Lane | None = None


class LaneRenameArgs(Args):
    lane: Lane
    name: LaneName


class LanesArgs(Args):
    count: Lane
    split: Literal["none", "budget", "bet", "both"] | None = None


class CleanupArgs(Args):
    days: int | None = Field(default=None, ge=1, le=30)


class UpdateArgs(Args):
    branch: Branch | None = None


class LogsArgs(Args):
    service: Literal["cli", "tg"] = "cli"
    lines: int = Field(default=200, ge=1, le=2000)


class FollowArgs(Args):
    service: Literal["cli", "tg"] = "cli"


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


VALUE_MAX_CHARS = 200
RULES_MAX_CHARS = 4000


class ConfigArgs(Args):
    pairs: dict[str, str] = Field(min_length=1, max_length=MAX_CONFIG_PAIRS)


class ConfigChange(Args):
    """One config edit: `lane` set overrides that lane, and the value `default` drops it."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    value: str = Field(max_length=RULES_MAX_CHARS, pattern=r"^[^\x00-\x1f\x7f]*$")
    lane: int | None = Field(default=None, ge=2, le=LANE_ID_MAX)

    @model_validator(mode="after")
    def _short_unless_rules(self) -> "ConfigChange":
        if self.key != "custom_rules_text" and len(self.value) > VALUE_MAX_CHARS:
            raise ValueError(f"value must be at most {VALUE_MAX_CHARS} characters")
        return self


class ConfigChanges(Args):
    changes: list[ConfigChange] = Field(min_length=1, max_length=MAX_CONFIG_PAIRS)

    @model_validator(mode="after")
    def _each_key_once(self) -> "ConfigChanges":
        targets = [(c.key, c.lane) for c in self.changes]
        if len(set(targets)) != len(targets):
            raise ValueError("each key may change once per lane")
        return self


OWNER_KEYS = frozenset(
    {
        "purge_days",
        "db_write_mode",
        "pipeline_depth",
        "events_enabled",
        "events_url",
        "delay_martin_threshold",
        "currency",
        "proxy",
        "key_ref",
        "proxy_ref",
    }
)
"""Base settings only the owner may change: those every lane shares, and the account."""


def owner_keys(body: ConfigChanges) -> list[str]:
    """The base changes in `body` that only the owner may make."""
    return sorted(c.key for c in body.changes if c.lane is None and c.key in OWNER_KEYS)


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

_UNITS = {
    "tg": ("the Telegram bot", "Telegram users get no replies until it starts again."),
}


def _unit_actions(group: str) -> dict[str, ControlAction]:
    """Start, stop, restart and status for one of the bot's side units (`wolfctl tg`)."""
    unit, down = _UNITS[group]
    named = unit[0].upper() + unit[1:]
    return {
        f"{group}_start": ControlAction(
            _BASIC,
            f"{group}.start",
            NoArgs,
            label="Start",
            title=f"Start {unit}?",
            effect=f"Starts {unit}. Betting is not touched.",
            confirm="Start",
        ),
        f"{group}_stop": ControlAction(
            _BASIC,
            f"{group}.stop",
            NoArgs,
            label="Stop",
            title=f"Stop {unit}?",
            effect=f"{down} Betting is not touched.",
            confirm="Stop",
        ),
        f"{group}_restart": ControlAction(
            _BASIC,
            f"{group}.restart",
            NoArgs,
            label="Restart",
            title=f"Restart {unit}?",
            effect=f"{named} is down for a few seconds. Betting is not touched.",
            confirm="Restart",
        ),
        f"{group}_status": ControlAction(
            _BASIC,
            f"{group}.status",
            NoArgs,
            label="Status",
            title=f"Status of {unit}?",
            effect=f"Shows whether {unit} is running. Changes nothing.",
            confirm="Status",
            confirm_first=False,
            exclusive=False,
        ),
    }


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
    "lane_stop": ControlAction(
        _BASIC,
        "lane_stop",
        LaneIdArgs,
        label="Stop",
        title="Stop lane {lane}?",
        effect="Lane {lane} stops after its current bet and its session is saved. Other lanes "
        "keep betting. Start brings it back.",
        confirm="Stop lane {lane}",
        lane_field="lane",
    ),
    "lane_start": ControlAction(
        _BASIC,
        "lane_start",
        LaneIdArgs,
        label="Start",
        title="Start lane {lane}?",
        effect="Lane {lane} starts betting again with its saved config.",
        confirm="Start lane {lane}",
        lane_field="lane",
    ),
    "lane_restart": ControlAction(
        _BASIC,
        "lane_restart",
        LaneIdArgs,
        label="Restart",
        title="Restart lane {lane}?",
        effect="Lane {lane} saves its session, stops and starts again with its saved config. "
        "Other lanes keep betting.",
        confirm="Restart lane {lane}",
        lane_field="lane",
    ),
    "lane_add": ControlAction(
        _CONFIG,
        "lane_add",
        LaneAddArgs,
        label="Add lane",
        title="Add the lane {name}?",
        effect="A new lane named {name} is added and starts betting. Other lanes keep betting.",
        confirm="Add lane",
        fresh=True,
    ),
    "lane_remove": ControlAction(
        _CONFIG,
        "lane_remove",
        RemovableLaneArgs,
        label="Remove",
        title="Remove lane {lane}?",
        effect="Lane {lane} stops now and its session is saved. It leaves the config with its "
        "overrides, and its number is not used again. Its history stays. Other lanes keep "
        "betting.",
        confirm="Remove lane {lane}",
        fresh=True,
        lane_field="lane",
    ),
    "lane_rename": ControlAction(
        _CONFIG,
        "lane_rename",
        LaneRenameArgs,
        label="Rename",
        title="Rename lane {lane} to {name}?",
        effect="Only the name changes. Lane {lane} keeps betting.",
        confirm="Rename",
        fresh=True,
        lane_field="lane",
    ),
    "pause": ControlAction(
        _BASIC,
        "pause",
        TargetArgs,
        label="Pause",
        title="Pause {target}?",
        effect="Betting stops on {target} until you resume it. The session stays open.",
        confirm="Pause {target}",
        lane_field="target",
    ),
    "resume": ControlAction(
        _BASIC,
        "resume",
        TargetArgs,
        label="Resume",
        title="Resume {target}?",
        effect="Betting starts again on {target} where it paused.",
        confirm="Resume {target}",
        lane_field="target",
    ),
    "reload": ControlAction(
        _BASIC,
        "reload",
        TargetArgs,
        label="Reload",
        title="Reload the config on {target}?",
        effect="The config file is read again on {target} without stopping. The session goes on.",
        confirm="Reload {target}",
        lane_field="target",
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
        effect="Lanes are added or stopped to reach {count} with the {split} split. The other "
        "lanes keep betting.",
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
        effect="The bot pulls the latest code{branch} and reinstalls. Each lane restarts on it "
        "when its session ends, so betting goes on. Use Restart under Service to move every "
        "lane now.",
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
    "archives": ControlAction(
        _BASIC,
        "archives",
        NoArgs,
        label="List archives",
        title="List the {service} archives?",
        effect="Lists the statistics databases earlier resets archived.",
        confirm="List archives",
        confirm_first=False,
        exclusive=False,
    ),
    "start_all": ControlAction(
        _BASIC,
        "start_all",
        NoArgs,
        label="Start all",
        title="Start every {service} bot?",
        effect="The bot starts betting with its saved config and the Telegram bot starts. Ones "
        "already running are left alone.",
        confirm="Start all",
    ),
    "status_all": ControlAction(
        _BASIC,
        "status_all",
        NoArgs,
        label="Check",
        title="Check the {service} services?",
        effect="Shows whether the betting bot and the Telegram bot run.",
        confirm="Check",
        confirm_first=False,
        exclusive=False,
    ),
    "version": ControlAction(
        _BASIC,
        "version",
        NoArgs,
        label="Version",
        title="Show the {service} version?",
        effect="Shows the bot and wolfctl versions. Changes nothing.",
        confirm="Version",
        confirm_first=False,
        exclusive=False,
    ),
    **_unit_actions("tg"),
}
