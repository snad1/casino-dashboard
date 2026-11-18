"""WolfBet's allowlisted commands: typed args, argv built from fixed paths, nothing else.

Reads call `wolfbet.py ... --json`; control and mutations go through `wolfctl`
with `--yes` where it would prompt. A user value reaches argv only after its
validator passed, always as its own argv item (config pairs as one `key=value`
item built from a schema key and a canonical value). Long-running commands are
listed with long_running=True and only run as jobs.
"""

import json
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from agent.runner import STDERR_TAIL, STDOUT_TAIL, RunResult, run, tail
from agent.validators import (
    Args,
    Branch,
    InvalidArgs,
    IsoDate,
    Lane,
    LaneTarget,
    Name,
    PositiveFloat,
    config_value,
    parse_args,
)

Runner = Callable[..., Awaitable[RunResult]]
Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
UNITS = {"cli": "wolfbet", "tg": "wolfbet-tg", "web": "wolfbet-web"}
MAX_CONFIG_PAIRS = 32


@dataclass(frozen=True)
class WolfBetPaths:
    """Absolute paths of everything the agent may run or write for WolfBet."""

    python: Path
    bot: Path
    wolfctl: Path
    repo: Path
    export_dir: Path
    journalctl: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "WolfBetPaths":
        home = Path(env.get("HOME") or Path.home())
        install = Path(env.get("CASINO_AGENT_WOLFBET_INSTALL_DIR") or home / "wolfbet-bot")
        state = Path(env.get("STATE_DIRECTORY") or home / ".local/state/casino-agent")
        paths = cls(
            python=install / "venv/bin/python3",
            bot=install / "wolfbet.py",
            wolfctl=Path(env.get("CASINO_AGENT_WOLFBET_WOLFCTL") or home / ".local/bin/wolfctl"),
            repo=Path(env.get("CASINO_AGENT_WOLFBET_REPO") or home / "wolfbet"),
            export_dir=Path(env.get("CASINO_AGENT_WOLFBET_EXPORT_DIR") or state / "exports"),
            journalctl=Path(env.get("CASINO_AGENT_JOURNALCTL") or "/usr/bin/journalctl"),
        )
        relative = [name for name, value in vars(paths).items() if not value.is_absolute()]
        if relative:
            raise ValueError(f"paths must be absolute: {', '.join(relative)}")
        return paths


# ── argument models ──
class NoArgs(Args):
    pass


class LaneArgs(Args):
    lane: Lane | None = None


class TargetArgs(Args):
    target: LaneTarget = "all"


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
    service: Literal["cli", "tg", "web"] = "cli"
    lines: int = Field(default=200, ge=1, le=2000)


class FollowArgs(Args):
    service: Literal["cli", "tg", "web"] = "cli"


class ConfigArgs(Args):
    pairs: dict[str, str] = Field(min_length=1, max_length=MAX_CONFIG_PAIRS)


# ── command table ──
Build = Callable[[Any, WolfBetPaths], list[str]]


@dataclass(frozen=True)
class CommandSpec:
    args: type[Args]
    build: Build
    timeout_s: float
    mutating: bool = False
    long_running: bool = False
    json_output: bool = False
    cwd_repo: bool = False
    clears_schema: bool = False


def _bot(p: WolfBetPaths, *items: str) -> list[str]:
    return [str(p.python), str(p.bot), *items]


def _ctl(p: WolfBetPaths, *items: str) -> list[str]:
    return [str(p.wolfctl), *items]


def _opt(flag: str, value: object | None) -> list[str]:
    return [] if value is None else [flag, str(value)]


def _window(flag: str) -> Build:
    def build(a: WindowArgs, p: WolfBetPaths) -> list[str]:
        return _bot(
            p,
            flag,
            "--json",
            *_opt("--since", a.since),
            *_opt("--until", a.until),
            *_opt("--lane", a.lane),
        )

    return build


def _export(a: ExportArgs, p: WolfBetPaths) -> list[str]:
    target = (
        p.export_dir / f"wolfbet-session-{a.session_id}.json"
        if a.format == "json"
        else p.export_dir
    )
    path = str(target) if a.format == "json" else str(target) + os.sep
    return _bot(p, "--export", path, *_opt("--export-session-id", a.session_id), "--json")


def _backtest(a: BacktestArgs, p: WolfBetPaths) -> list[str]:
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


def _ctl_verb(*items: str) -> Build:
    return lambda _a, p: _ctl(p, *items)


def _signal(verb: str) -> Build:
    return lambda a, p: _ctl(p, verb, str(a.target))


COMMANDS: dict[str, CommandSpec] = {
    "start": CommandSpec(NoArgs, _ctl_verb("start"), 60, mutating=True),
    "stop": CommandSpec(NoArgs, _ctl_verb("stop"), 60, mutating=True),
    "restart": CommandSpec(NoArgs, _ctl_verb("restart"), 120, mutating=True, long_running=True),
    "status": CommandSpec(
        LaneArgs,
        lambda a, p: _bot(p, "--status", "--json", *_opt("--lane", a.lane)),
        30,
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
    "export": CommandSpec(ExportArgs, _export, 600, long_running=True, json_output=True),
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
    **{
        f"{group}.{verb}": CommandSpec(
            NoArgs, _ctl_verb(group, verb), 60, mutating=verb != "status"
        )
        for group in ("tg", "web")
        for verb in ("start", "stop", "restart", "status")
    },
}


def result_data(spec: CommandSpec, result: RunResult) -> dict[str, Any]:
    """The reply body: exit code, the JSON document when there is one, else stdout; stderr tail."""
    data: dict[str, Any] = {
        "exit_code": result.exit_code,
        "stderr": tail(result.stderr, STDERR_TAIL),
    }
    if spec.json_output:
        try:
            doc = json.loads(result.stdout)
        except json.JSONDecodeError:
            doc = None
        if isinstance(doc, dict):
            data["json"] = doc
            return data
    data["stdout"] = tail(result.stdout, STDOUT_TAIL)
    return data


@dataclass
class WolfBetService:
    """Runs WolfBet commands for the agent; keeps the config schema between calls."""

    paths: WolfBetPaths
    runner: Runner = run
    _schema: dict[str, Any] | None = field(default=None, repr=False)

    async def run_command(self, name: str, raw_args: dict[str, Any]) -> dict[str, Any]:
        spec = COMMANDS[name]
        args = parse_args(spec.args, raw_args)
        argv = spec.build(args, self.paths)
        result = await self.runner(
            argv, spec.timeout_s, cwd=self.paths.repo if spec.cwd_repo else None
        )
        if spec.clears_schema:
            self._schema = None
        return result_data(spec, result)

    async def schema(self) -> dict[str, Any]:
        if self._schema is None:
            data = await self.run_command("config_schema", {})
            if data.get("exit_code") != 0 or "json" not in data:
                raise RuntimeError("wolfbet.py --config-schema failed")
            self._schema = data["json"]
        return self._schema

    async def config_pairs(self, raw_args: dict[str, Any]) -> list[str]:
        """`key=value` argv items for a config change, every key and value checked."""
        args = parse_args(ConfigArgs, raw_args)
        schema = await self.schema()
        return [config_pair(schema, key, value) for key, value in args.pairs.items()]

    async def config_dry_run(self, raw_args: dict[str, Any]) -> dict[str, Any]:
        argv = _bot(
            self.paths, "--dry-run", "--json", "--config-set", *await self.config_pairs(raw_args)
        )
        return result_data(_CONFIG_SPEC, await self.runner(argv, _CONFIG_SPEC.timeout_s))

    async def config_set(self, raw_args: dict[str, Any]) -> dict[str, Any]:
        pairs = await self.config_pairs(raw_args)
        dry = result_data(
            _CONFIG_SPEC,
            await self.runner(
                _bot(self.paths, "--dry-run", "--json", "--config-set", *pairs),
                _CONFIG_SPEC.timeout_s,
            ),
        )
        if dry["exit_code"] != 0:
            return {"dry_run": dry, "set": None}
        done = await self.runner(_bot(self.paths, "--config-set", *pairs), _CONFIG_SPEC.timeout_s)
        return {"dry_run": dry, "set": result_data(_TEXT_SPEC, done)}

    def handlers(self) -> dict[str, Handler]:
        """Agent commands for every short WolfBet command plus the config flow."""

        def bound(name: str) -> Handler:
            return lambda raw: self.run_command(name, raw)

        out = {
            f"wolfbet.{name}": bound(name)
            for name, spec in COMMANDS.items()
            if not spec.long_running
        }
        out["wolfbet.config_dry_run"] = self.config_dry_run
        out["wolfbet.config_set"] = self.config_set
        return out


_CONFIG_SPEC = CommandSpec(ConfigArgs, lambda _a, _p: [], 60, mutating=True, json_output=True)
_TEXT_SPEC = CommandSpec(NoArgs, lambda _a, _p: [], 60)


def config_pair(schema: dict[str, Any], key: str, value: str) -> str:
    """One validated `key=value` item: a known key (or alias), lanes, lane_split or laneK.key."""
    lanes = schema["lanes"]
    keys = {k["key"]: k for k in schema["keys"]}
    aliases = {alias: k["key"] for k in schema["keys"] for alias in k["aliases"]}
    name = key.strip().lower()
    if name == "lanes":
        count = config_value({"key": "lanes", "type": "integer", **lanes["count"]}, value)
        return f"lanes={count}"
    if name == "lane_split":
        split = config_value(
            {"key": "lane_split", "type": "string", "choices": lanes["split"]["choices"]}, value
        )
        return f"lane_split={split}"
    lane_prefix = ""
    if name.startswith("lane") and "." in name:
        head, name = name.split(".", 1)
        number = head[4:]
        if not number.isdigit() or not (
            lanes["override_lanes"]["min"] <= int(number) <= lanes["override_lanes"]["max"]
        ):
            raise InvalidArgs("lane overrides are laneK.key with K in the allowed lanes")
        lane_prefix = f"lane{int(number)}."
    entry = keys.get(aliases.get(name, name))
    if entry is None:
        raise InvalidArgs("unknown config key")
    if lane_prefix:
        if not entry["lane_overridable"]:
            raise InvalidArgs(f"{entry['key']} cannot differ per lane")
        if value.strip().lower() in lanes["unset_values"]:
            return f"{lane_prefix}{entry['key']}=default"
    return f"{lane_prefix}{entry['key']}={config_value(entry, value)}"
