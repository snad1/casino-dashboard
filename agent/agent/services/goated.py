"""goated's allowlisted commands: only the verbs goatedctl and goated.py have.

Reads call `goated.py ... --json`; control goes through `goatedctl`. goated has no live IPC
(no pause, resume or reload); `goatedctl lanes`, `cleanup` and `update` never prompt, so
they take no `--yes`; its benchmark times 0-amount bets and asks first, so the agent passes
`--yes` once the dashboard has confirmed; its backtest and export print text; an export
writes one session. WolfBet's argument models are reused where goated takes the same flags.
Running and checking a command lives in `agent.services.common`.
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
    CommandSpec,
    KeyIdArgs,
    KeyPutArgs,
    KeyRotateArgs,
    LaneAddArgs,
    LaneArgs,
    LaneIdArgs,
    LaneRenameArgs,
    NoArgs,
    RemovableLaneArgs,
    _bot,
    _ctl,
    _ctl_verb,
    _opt,
)
from agent.services.wolfbet import (
    CleanupArgs,
    DoctorArgs,
    FollowArgs,
    LanesArgs,
    LastBetsArgs,
    LogsArgs,
    PresetArgs,
    SessionArgs,
    WindowArgs,
    _window,
)
from agent.validators import Args, Name

__all__ = ["COMMANDS", "UNIT", "UNITS", "GoatedService", "build", "paths_from_env"]

UNITS = {"cli": "goated", "tg": "goated-tg"}
UNIT = "goated"


def paths_from_env(env: Mapping[str, str] = os.environ) -> BotPaths:
    return BotPaths.from_env(
        "goated",
        env,
        bot_file="goated.py",
        install_dir="goated-bot",
        repo_dir="goated",
        ctl_name="goatedctl",
    )


class GoatedBenchmarkArgs(Args):
    count: int = Field(default=20, ge=3, le=200)


class GoatedBacktestArgs(Args):
    simulate: int | None = Field(default=None, ge=1, le=5_000_000)
    replay_session: int | None = Field(default=None, ge=1)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)
    preset: Name | None = None

    @model_validator(mode="after")
    def _one_source(self) -> "GoatedBacktestArgs":
        if self.simulate is not None and self.replay_session is not None:
            raise ValueError("simulate and replay_session are exclusive")
        return self


class GoatedExportArgs(Args):
    """goated writes one session per file, its format from the file's extension."""

    session_id: int = Field(ge=1)
    format: Literal["csv", "json"] = "csv"


def _export(a: GoatedExportArgs, p: BotPaths) -> list[str]:
    target = p.export_dir / f"goated-session-{a.session_id}.{a.format}"
    return _bot(p, "--export", str(target), "--export-session-id", str(a.session_id))


def _backtest(a: GoatedBacktestArgs, p: BotPaths) -> list[str]:
    return _bot(
        p,
        "--backtest",
        *_opt("--simulate", a.simulate),
        *_opt("--replay-session", a.replay_session),
        *_opt("--seed", a.seed),
        *_opt("--preset", a.preset),
    )


def _journal(a: LogsArgs | FollowArgs, p: BotPaths, *tail: str) -> list[str]:
    return [str(p.journalctl), "--user", "-u", UNITS[a.service], *tail, "--no-pager"]


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
    "lanes": CommandSpec(
        LanesArgs,
        lambda a, p: _ctl(p, "lanes", str(a.count), *_opt("--split", a.split)),
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
    "export": CommandSpec(GoatedExportArgs, _export, 600, long_running=True, writes_file=True),
    "cleanup": CommandSpec(
        CleanupArgs,
        lambda a, p: _ctl(p, "cleanup", *([str(a.days)] if a.days else [])),
        300,
        mutating=True,
    ),
    "backtest": CommandSpec(GoatedBacktestArgs, _backtest, 3600, long_running=True),
    "benchmark": CommandSpec(
        GoatedBenchmarkArgs,
        lambda a, p: _bot(p, "--benchmark", str(a.count), "--yes"),
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
        NoArgs,
        _ctl_verb("update"),
        1800,
        mutating=True,
        long_running=True,
        cwd_repo=True,
        clears_schema=True,
    ),
    "logs": CommandSpec(LogsArgs, lambda a, p: _journal(a, p, "-n", str(a.lines)), 30),
    "logs_follow": CommandSpec(
        FollowArgs, lambda a, p: _journal(a, p, "-n", "50", "-f"), 86400, long_running=True
    ),
    "start_all": CommandSpec(NoArgs, _ctl_verb("start-all"), 120, mutating=True),
    "stop_all": CommandSpec(NoArgs, _ctl_verb("stop-all"), 120, mutating=True),
    "status_all": CommandSpec(NoArgs, _ctl_verb("status-all"), 30),
    "version": CommandSpec(NoArgs, _ctl_verb("version"), 30),
    **{
        f"tg.{verb}": CommandSpec(NoArgs, _ctl_verb("tg", verb), 60, mutating=verb != "status")
        for verb in ("start", "stop", "restart", "status")
    },
}


@dataclass
class GoatedService(BotService):
    """goated's commands, under `goated.*`."""

    name: str = "goated"
    commands: Mapping[str, CommandSpec] = field(default_factory=lambda: COMMANDS)


def build(env: Mapping[str, str], busy: Callable[[], bool], files: FileStore | None) -> BotService:
    return GoatedService(paths_from_env(env), busy=busy, files=files)
