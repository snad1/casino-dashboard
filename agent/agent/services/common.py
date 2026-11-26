"""What every bot's command module shares: paths, the command table type, argv builders,
the service that validates and runs commands, and the config-pair checks.

A bot module declares its paths' defaults and its command table; everything that runs a
command lives here, so each bot gets the same allowlist rules: typed args, argv built from
fixed absolute paths, a user value only after its validator passed and always as its own
argv item, secrets on stdin only.
"""

import json
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from agent.errors import Busy
from agent.files import FileStore
from agent.runner import STDERR_TAIL, STDOUT_TAIL, RunResult, run, tail
from agent.validators import (
    LANE_ID_MAX,
    Args,
    InvalidArgs,
    InvalidValue,
    KeyId,
    KeyLabel,
    KeySecret,
    Lane,
    LaneName,
    LaneTarget,
    config_value,
    parse_args,
)

Runner = Callable[..., Awaitable[RunResult]]
Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
MAX_CONFIG_PAIRS = 32


@dataclass(frozen=True)
class BotPaths:
    """Absolute paths of everything the agent may run or write for one bot."""

    python: Path
    bot: Path
    ctl: Path
    repo: Path
    export_dir: Path
    journalctl: Path

    @classmethod
    def from_env(
        cls,
        service: str,
        env: Mapping[str, str],
        *,
        bot_file: str,
        install_dir: str,
        repo_dir: str,
        ctl_name: str,
        ctl_env: tuple[str, ...] = (),
    ) -> "BotPaths":
        """The bot's paths from `CASINO_AGENT_<SERVICE>_INSTALL_DIR`, `_CTL`, `_REPO` and
        `_EXPORT_DIR`, else under HOME; `ctl_env` names older variables still read for the
        ctl. A relative path is refused with the variable that set it."""
        prefix = f"CASINO_AGENT_{service.upper()}_"
        home = Path(env.get("HOME") or Path.home())
        state = Path(env.get("STATE_DIRECTORY") or home / ".local/state/casino-agent")

        def pick(names: tuple[str, ...], default: Path) -> tuple[Path, str]:
            for name in names:
                if env.get(name):
                    return Path(env[name]), name
            return default, names[0]

        install, install_var = pick((f"{prefix}INSTALL_DIR",), home / install_dir)
        ctl, ctl_var = pick((f"{prefix}CTL", *ctl_env), home / ".local/bin" / ctl_name)
        repo, repo_var = pick((f"{prefix}REPO",), home / repo_dir)
        export, export_var = pick((f"{prefix}EXPORT_DIR",), state / "exports")
        journalctl, journal_var = pick(("CASINO_AGENT_JOURNALCTL",), Path("/usr/bin/journalctl"))
        relative = [
            var
            for var, value in (
                (install_var, install),
                (ctl_var, ctl),
                (repo_var, repo),
                (export_var, export),
                (journal_var, journalctl),
            )
            if not value.is_absolute()
        ]
        if relative:
            raise ValueError(f"paths must be absolute: {', '.join(relative)}")
        return cls(
            python=install / "venv/bin/python3",
            bot=install / bot_file,
            ctl=ctl,
            repo=repo,
            export_dir=export,
            journalctl=journalctl,
        )


# ── argument models ──
class NoArgs(Args):
    pass


class LaneArgs(Args):
    lane: Lane | None = None


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


class KeyIdArgs(Args):
    key_id: KeyId


class KeyPutArgs(Args):
    key_id: KeyId
    label: KeyLabel
    secret: KeySecret
    owner_user_id: int | None = Field(default=None, ge=1)
    kind: Literal["key", "proxy", "telegram"] = "key"


class KeyRotateArgs(Args):
    key_id: KeyId
    secret: KeySecret


class ConfigArgs(Args):
    pairs: dict[str, str] = Field(min_length=1, max_length=MAX_CONFIG_PAIRS)


# ── command table ──
Build = Callable[[Any, BotPaths], list[str]]


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
    stdin_field: str | None = None
    writes_file: bool = False


def _bot(p: BotPaths, *items: str) -> list[str]:
    return [str(p.python), str(p.bot), *items]


def _ctl(p: BotPaths, *items: str) -> list[str]:
    return [str(p.ctl), *items]


def _opt(flag: str, value: object | None) -> list[str]:
    return [] if value is None else [flag, str(value)]


def _ctl_verb(*items: str) -> Build:
    return lambda _a, p: _ctl(p, *items)


def _signal(verb: str) -> Build:
    return lambda a, p: _ctl(p, verb, str(a.target))


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


@dataclass(frozen=True)
class Prepared:
    """A validated command ready to run: its argv and how to run it."""

    name: str
    args: dict[str, Any]
    argv: list[str]
    timeout_s: float
    cwd: Path | None
    mutating: bool
    file_id: str | None = None
    redact: tuple[str, ...] = ()
    stdin: bytes | None = field(default=None, repr=False)


def _never_busy() -> bool:
    return False


@dataclass
class BotService:
    """Runs one bot's commands for the agent; keeps the config schema between calls.

    `busy` tells whether a mutating job runs for the bot; a direct mutating command
    is then refused with Busy, so it cannot interleave with an update or a restart.
    """

    paths: BotPaths
    runner: Runner = run
    busy: Callable[[], bool] = _never_busy
    files: FileStore | None = None
    name: str = "bot"
    commands: Mapping[str, CommandSpec] = field(default_factory=dict)
    _schema: dict[str, Any] | None = field(default=None, repr=False)

    def long_running(self, name: str) -> bool:
        return name in self.commands and self.commands[name].long_running

    def prepare(self, name: str, raw_args: dict[str, Any]) -> Prepared:
        """Validate `raw_args` for command `name`; raises InvalidArgs (unknown name included)."""
        spec = self.commands.get(name)
        if spec is None:
            raise InvalidArgs(f"unknown {self.name} command")
        args = parse_args(spec.args, raw_args)
        paths, file_id = self.paths, None
        redact: tuple[str, ...] = ()
        if spec.writes_file:
            if self.files is None:
                raise RuntimeError(f"{name} needs a file store")
            file_id, directory = self.files.allocate()
            paths = replace(self.paths, export_dir=directory)
            redact = tuple({str(directory), os.path.realpath(directory)})
        secret = getattr(args, spec.stdin_field) if spec.stdin_field else None
        return Prepared(
            name=name,
            args=args.model_dump(
                mode="json", exclude={spec.stdin_field} if spec.stdin_field else None
            ),
            argv=spec.build(args, paths),
            timeout_s=spec.timeout_s,
            cwd=self.paths.repo if spec.cwd_repo else None,
            mutating=spec.mutating,
            file_id=file_id,
            redact=redact,
            stdin=None if secret is None else f"{secret}\n".encode(),
        )

    def after_job(self, name: str) -> None:
        """Forget what a finished command may have changed (the config schema after update)."""
        if self.commands[name].clears_schema:
            self._schema = None

    def _refuse_while_busy(self) -> None:
        if self.busy():
            raise Busy(f"a mutating {self.name} job is running")

    async def run_command(self, name: str, raw_args: dict[str, Any]) -> dict[str, Any]:
        prepared = self.prepare(name, raw_args)
        if prepared.mutating:
            self._refuse_while_busy()
        extra = {} if prepared.stdin is None else {"stdin": prepared.stdin}
        result = await self.runner(prepared.argv, prepared.timeout_s, cwd=prepared.cwd, **extra)
        self.after_job(name)
        return result_data(self.commands[name], result)

    async def schema(self) -> dict[str, Any]:
        if self._schema is None:
            data = await self.run_command("config_schema", {})
            if data.get("exit_code") != 0 or "json" not in data:
                raise RuntimeError(f"{self.paths.bot.name} --config-schema failed")
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
        self._refuse_while_busy()
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
        """Agent commands for every short command plus the config flow."""

        def bound(name: str) -> Handler:
            return lambda raw: self.run_command(name, raw)

        out = {
            f"{self.name}.{name}": bound(name)
            for name, spec in self.commands.items()
            if not spec.long_running
        }
        out[f"{self.name}.config_dry_run"] = self.config_dry_run
        out[f"{self.name}.config_set"] = self.config_set
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
            raise InvalidValue(entry["key"], "cannot differ per lane")
        if value.strip().lower() in lanes["unset_values"]:
            return f"{lane_prefix}{entry['key']}=default"
    return f"{lane_prefix}{entry['key']}={config_value(entry, value)}"
