"""WolfBet's allowlisted commands: typed args, argv built from fixed paths, nothing else.

Reads call `wolfbet.py ... --json`; control and mutations go through `wolfctl`
with `--yes` where it would prompt. A user value reaches argv only after its
validator passed, always as its own argv item (config pairs as one `key=value`
item built from a schema key and a canonical value). Long-running commands are
listed with long_running=True and only run as jobs. Running and checking a command lives
in `agent.services.common`.
"""

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field, model_validator

from agent.files import FileStore
from agent.services.common import (
    BotPaths,
    BotService,
    Build,
    CommandSpec,
    ConfigArgs,
    KeyIdArgs,
    KeyPutArgs,
    KeyRotateArgs,
    LaneAddArgs,
    LaneArgs,
    LaneIdArgs,
    LaneRenameArgs,
    NoArgs,
    RemovableLaneArgs,
    TargetArgs,
    _bot,
    _ctl,
    _ctl_verb,
    _opt,
    _signal,
    config_pair,
)
from agent.validators import Args, Branch, IsoDate, Lane, Name, PositiveFloat

__all__ = [
    "COMMANDS",
    "UNIT",
    "UNITS",
    "ConfigArgs",
    "WolfBetPaths",
    "WolfBetService",
    "build",
    "config_pair",
    "paths_from_env",
]

UNITS = {"cli": "wolfbet", "tg": "wolfbet-tg"}
UNIT = "wolfbet"

WolfBetPaths = BotPaths


def paths_from_env(env: Mapping[str, str] = os.environ) -> BotPaths:
    """WolfBet's paths; `CASINO_AGENT_WOLFBET_WOLFCTL` is still read for the ctl."""
    return BotPaths.from_env(
        "wolfbet",
        env,
        bot_file="wolfbet.py",
        install_dir="wolfbet-bot",
        repo_dir="wolfbet",
        ctl_name="wolfctl",
        ctl_env=("CASINO_AGENT_WOLFBET_WOLFCTL",),
    )


class LanesArgs(Args):
    count: Lane
    split: Literal["none", "budget", "bet", "both"] | None = None


class WindowArgs(Args):
    since: IsoDate | None = None
    until: IsoDate | None = None
    lane: Lane | None = None


class LastBetsArgs(Args):
    count: int = Field(default=20, ge=1, le=1000)


class SessionArgs(Args):
    session_id: int = Field(ge=1)


class PresetArgs(Args):
    name: Name


class ExportArgs(Args):
    session_id: int | None = Field(default=None, ge=1)
    format: Literal["csv", "json"] = "csv"

    @model_validator(mode="after")
    def _json_needs_a_session(self) -> "ExportArgs":
        if self.format == "json" and self.session_id is None:
            raise ValueError("a JSON export needs session_id")
        return self


class CleanupArgs(Args):
    days: int | None = Field(default=None, ge=1, le=30)


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


class BenchmarkArgs(Args):
    count: int = Field(default=20, ge=3, le=200)
    lanes: Lane | None = None


class DoctorArgs(Args):
    fix: bool = False


class UpdateArgs(Args):
    branch: Branch | None = None


class LogsArgs(Args):
    service: Literal["cli", "tg"] = "cli"
    lines: int = Field(default=200, ge=1, le=2000)


class FollowArgs(Args):
    service: Literal["cli", "tg"] = "cli"


def _window(flag: str) -> Build:
    def build(a: WindowArgs, p: BotPaths) -> list[str]:
        return _bot(
            p,
            flag,
            "--json",
            *_opt("--since", a.since),
            *_opt("--until", a.until),
            *_opt("--lane", a.lane),
        )

    return build


def _export(a: ExportArgs, p: BotPaths) -> list[str]:
    target = (
        p.export_dir / f"wolfbet-session-{a.session_id}.json"
        if a.format == "json"
        else p.export_dir
    )
    path = str(target) if a.format == "json" else str(target) + os.sep
    return _bot(p, "--export", path, *_opt("--export-session-id", a.session_id), "--json")


def _backtest(a: BacktestArgs, p: BotPaths) -> list[str]:
    return _bot(
        p,
        "--backtest",
        "--json",
        *_opt("--simulate", a.simulate),
        *_opt("--replay-session", a.replay_session),
        *_opt("--seed", a.seed),
        *_opt("--sessions", a.sessions),
        *_opt("--runs", a.runs),
        *_opt("--balance", a.balance),
        *_opt("--preset", a.preset),
    )


COMMANDS: dict[str, CommandSpec] = {
    "start": CommandSpec(NoArgs, _ctl_verb("start"), 60, mutating=True),
    "stop": CommandSpec(NoArgs, _ctl_verb("stop"), 60, mutating=True),
    "restart": CommandSpec(NoArgs, _ctl_verb("restart"), 120, mutating=True, long_running=True),
    "restart_rolling": CommandSpec(NoArgs, _ctl_verb("restart", "--rolling"), 60, mutating=True),
    "status": CommandSpec(
        LaneArgs,
        lambda a, p: _bot(p, "--status", "--json", *_opt("--lane", a.lane)),
        30,
        json_output=True,
    ),
    "lane_stop": CommandSpec(
        LaneIdArgs, lambda a, p: _ctl(p, "stop", str(a.lane)), 60, mutating=True
    ),
    "lane_start": CommandSpec(
        LaneIdArgs, lambda a, p: _ctl(p, "start", str(a.lane)), 60, mutating=True
    ),
    "lane_restart": CommandSpec(
        LaneIdArgs, lambda a, p: _ctl(p, "restart", str(a.lane)), 60, mutating=True
    ),
    "lane_restart_end": CommandSpec(
        LaneIdArgs,
        lambda a, p: _ctl(p, "restart", str(a.lane), "--at-boundary"),
        30,
        mutating=True,
    ),
    "lane_add": CommandSpec(
        LaneAddArgs,
        lambda a, p: _ctl(p, "lane", "add", a.name, *_opt("--from", a.copy_from)),
        60,
        mutating=True,
    ),
    "lane_remove": CommandSpec(
        RemovableLaneArgs, lambda a, p: _ctl(p, "lane", "remove", str(a.lane)), 60, mutating=True
    ),
    "lane_rename": CommandSpec(
        LaneRenameArgs,
        lambda a, p: _ctl(p, "lane", "rename", str(a.lane), a.name),
        60,
        mutating=True,
    ),
    "key_list": CommandSpec(
        NoArgs, lambda _a, p: _bot(p, "--key-list", "--json"), 30, json_output=True
    ),
    "key_put": CommandSpec(
        KeyPutArgs,
        lambda a, p: _bot(
            p,
            "--key-put",
            a.key_id,
            f"--key-label={a.label}",
            f"--key-kind={a.kind}",
            *_opt("--key-owner", a.owner_user_id),
            "--json",
        ),
        30,
        mutating=True,
        json_output=True,
        stdin_field="secret",
    ),
    "key_rotate": CommandSpec(
        KeyRotateArgs,
        lambda a, p: _bot(p, "--key-rotate", a.key_id, "--json"),
        30,
        mutating=True,
        json_output=True,
        stdin_field="secret",
    ),
    "key_delete": CommandSpec(
        KeyIdArgs,
        lambda a, p: _bot(p, "--key-delete", a.key_id, "--json"),
        30,
        mutating=True,
        json_output=True,
    ),
    "pause": CommandSpec(TargetArgs, _signal("pause"), 30, mutating=True),
    "resume": CommandSpec(TargetArgs, _signal("resume"), 30, mutating=True),
    "reload": CommandSpec(TargetArgs, _signal("reload"), 30, mutating=True),
    "lanes": CommandSpec(
        LanesArgs,
        lambda a, p: _ctl(p, "lanes", str(a.count), "--yes", *_opt("--split", a.split)),
        300,
        mutating=True,
        long_running=True,
    ),
    "config_show": CommandSpec(
        NoArgs, lambda _a, p: _bot(p, "--config-show", "--json"), 30, json_output=True
    ),
    "config_schema": CommandSpec(
        NoArgs, lambda _a, p: _bot(p, "--config-schema"), 30, json_output=True
    ),
    "lane_ids": CommandSpec(NoArgs, lambda _a, p: _bot(p, "--lane-ids", "all"), 30),
    "lane_ids_enabled": CommandSpec(NoArgs, lambda _a, p: _bot(p, "--lane-ids", "enabled"), 30),
    "run_reset": CommandSpec(NoArgs, _ctl_verb("run-reset", "--yes"), 120, mutating=True),
    "reset": CommandSpec(NoArgs, _ctl_verb("reset", "--yes"), 120, mutating=True),
    "archives": CommandSpec(NoArgs, _ctl_verb("archives"), 30),
    "stats": CommandSpec(WindowArgs, _window("--stats"), 60, json_output=True),
    "analytics": CommandSpec(WindowArgs, _window("--analytics"), 60, json_output=True),
    "last_bets": CommandSpec(
        LastBetsArgs,
        lambda a, p: _bot(p, "--last-bets", str(a.count), "--json"),
        30,
        json_output=True,
    ),
    "session": CommandSpec(
        SessionArgs,
        lambda a, p: _bot(p, "--session-bets", str(a.session_id), "--json"),
        30,
        json_output=True,
    ),
    "presets": CommandSpec(NoArgs, lambda _a, p: _bot(p, "--list-presets"), 30),
    "preset": CommandSpec(PresetArgs, lambda a, p: _ctl(p, "preset", a.name), 60, mutating=True),
    "export": CommandSpec(
        ExportArgs, _export, 600, long_running=True, json_output=True, writes_file=True
    ),
    "cleanup": CommandSpec(
        CleanupArgs,
        lambda a, p: _ctl(p, "cleanup", "--yes", *([str(a.days)] if a.days else [])),
        300,
        mutating=True,
    ),
    "backtest": CommandSpec(BacktestArgs, _backtest, 3600, long_running=True, json_output=True),
    "benchmark": CommandSpec(
        BenchmarkArgs,
        lambda a, p: _bot(p, "--benchmark", str(a.count), *_opt("--lanes", a.lanes)),
        600,
        long_running=True,
    ),
    "diagnose": CommandSpec(NoArgs, lambda _a, p: _bot(p, "--diagnose"), 60),
    "doctor": CommandSpec(
        DoctorArgs,
        lambda a, p: _ctl(p, "doctor", *(["--fix", "--yes"] if a.fix else [])),
        600,
        mutating=True,
        long_running=True,
    ),
    "update": CommandSpec(
        UpdateArgs,
        lambda a, p: _ctl(p, "update", "--yes", *_opt("--branch", a.branch)),
        1800,
        mutating=True,
        long_running=True,
        cwd_repo=True,
        clears_schema=True,
    ),
    "logs": CommandSpec(
        LogsArgs,
        lambda a, p: [
            str(p.journalctl),
            "--user",
            "-u",
            UNITS[a.service],
            "-n",
            str(a.lines),
            "--no-pager",
        ],
        30,
    ),
    "logs_follow": CommandSpec(
        FollowArgs,
        lambda a, p: [
            str(p.journalctl),
            "--user",
            "-u",
            UNITS[a.service],
            "-n",
            "50",
            "-f",
            "--no-pager",
        ],
        86400,
        long_running=True,
    ),
    "start_all": CommandSpec(NoArgs, _ctl_verb("start-all"), 120, mutating=True),
    "stop_all": CommandSpec(NoArgs, _ctl_verb("stop-all"), 120, mutating=True),
    "status_all": CommandSpec(NoArgs, _ctl_verb("status-all"), 30),
    "version": CommandSpec(NoArgs, _ctl_verb("version"), 30),
    **{
        f"{group}.{verb}": CommandSpec(
            NoArgs, _ctl_verb(group, verb), 60, mutating=verb != "status"
        )
        for group in ("tg",)
        for verb in ("start", "stop", "restart", "status")
    },
}


@dataclass
class WolfBetService(BotService):
    """WolfBet's commands, under `wolfbet.*`."""

    name: str = "wolfbet"
    commands: Mapping[str, CommandSpec] = field(default_factory=lambda: COMMANDS)


def build(env: Mapping[str, str], busy: Callable[[], bool], files: FileStore | None) -> BotService:
    return WolfBetService(paths_from_env(env), busy=busy, files=files)
