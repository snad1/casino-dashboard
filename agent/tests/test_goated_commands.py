"""goated's allowlist: the exact argv per command, only the verbs goatedctl and goated.py have,
secrets on stdin only, and the config flow on goated's own schema.

The config schema fixture is `goated.py --config-schema` from CLI 1.23.0.
"""

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from agent.cli import installed_services
from agent.jobs import JobManager
from agent.runner import RunResult
from agent.services.common import BotPaths
from agent.services.goated import COMMANDS, GoatedService, paths_from_env
from agent.services.wolfbet import COMMANDS as WOLFBET_COMMANDS
from agent.validators import InvalidArgs
from tests.test_wolfbet_commands import EXP, FILE_ID, FakeRunner, StubFiles

FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA = json.loads((FIXTURES / "goated-config-schema.json").read_text())
PATHS = BotPaths(
    python=Path("/opt/gt/venv/bin/python3"),
    bot=Path("/opt/gt/goated.py"),
    ctl=Path("/opt/bin/goatedctl"),
    repo=Path("/opt/goated"),
    export_dir=Path("/var/agent/exports"),
    journalctl=Path("/usr/bin/journalctl"),
)
PY, BOT, CTL, JCTL = (
    "/opt/gt/venv/bin/python3",
    "/opt/gt/goated.py",
    "/opt/bin/goatedctl",
    ("/usr/bin/journalctl"),
)
KEY_SECRET = "gt_live_FRIEND_0123456789abcdef7777"  # noqa: S105  # a fake key for tests


class GoatedRunner(FakeRunner):
    async def __call__(
        self, argv: list[str], timeout_s: float, cwd: Path | None = None, stdin: bytes | None = None
    ) -> RunResult:
        if argv[-1] == "--config-schema" and not self.results:
            self.calls.append((list(argv), timeout_s, cwd))
            self.stdins.append(stdin)
            return RunResult(0, json.dumps(SCHEMA), "")
        return await super().__call__(argv, timeout_s, cwd, stdin)


def _service(runner: FakeRunner) -> GoatedService:
    return GoatedService(PATHS, runner, files=StubFiles())


ARGV_CASES: list[tuple[str, dict[str, Any], list[str]]] = [
    ("start", {}, [CTL, "start"]),
    ("stop", {}, [CTL, "stop"]),
    ("restart", {}, [CTL, "restart"]),
    ("restart_rolling", {}, [CTL, "restart", "--rolling"]),
    ("status", {}, [PY, BOT, "--status", "--json"]),
    ("status", {"lane": 3}, [PY, BOT, "--status", "--json", "--lane", "3"]),
    ("lane_stop", {"lane": 2}, [CTL, "stop", "2"]),
    ("lane_start", {"lane": 99}, [CTL, "start", "99"]),
    ("lane_restart", {"lane": 2}, [CTL, "restart", "2"]),
    ("lane_restart_end", {"lane": 5}, [CTL, "restart", "5", "--at-boundary"]),
    (
        "lane_add",
        {"name": "night run", "copy_from": 2},
        [CTL, "lane", "add", "night run", "--from", "2"],
    ),
    ("lane_remove", {"lane": 3}, [CTL, "lane", "remove", "3"]),
    ("lane_rename", {"lane": 3, "name": "fast"}, [CTL, "lane", "rename", "3", "fast"]),
    ("key_list", {}, [PY, BOT, "--key-list", "--json"]),
    (
        "key_put",
        {"key_id": "friend", "label": "Friend", "secret": KEY_SECRET, "owner_user_id": 7},
        [
            PY,
            BOT,
            "--key-put",
            "friend",
            "--key-label=Friend",
            "--key-kind=key",
            "--key-owner",
            "7",
            "--json",
        ],
    ),
    (
        "key_rotate",
        {"key_id": "friend", "secret": KEY_SECRET},
        [PY, BOT, "--key-rotate", "friend", "--json"],
    ),
    ("key_delete", {"key_id": "friend"}, [PY, BOT, "--key-delete", "friend", "--json"]),
    ("lanes", {"count": 3}, [CTL, "lanes", "3"]),
    ("lanes", {"count": 3, "split": "budget"}, [CTL, "lanes", "3", "--split", "budget"]),
    ("config_show", {}, [PY, BOT, "--config-show", "--json"]),
    ("config_schema", {}, [PY, BOT, "--config-schema"]),
    ("lane_ids", {}, [PY, BOT, "--lane-ids", "all"]),
    ("lane_ids_enabled", {}, [PY, BOT, "--lane-ids", "enabled"]),
    ("run_reset", {}, [CTL, "run-reset", "--yes"]),
    ("reset", {}, [CTL, "reset", "--yes"]),
    ("archives", {}, [CTL, "archives"]),
    (
        "stats",
        {"since": "2026-11-01", "lane": 2},
        [PY, BOT, "--stats", "--json", "--since", "2026-11-01", "--lane", "2"],
    ),
    ("analytics", {}, [PY, BOT, "--analytics", "--json"]),
    ("last_bets", {"count": 5}, [PY, BOT, "--last-bets", "5", "--json"]),
    ("session", {"session_id": 12}, [PY, BOT, "--session-bets", "12", "--json"]),
    ("presets", {}, [PY, BOT, "--list-presets"]),
    ("preset", {"name": "safe"}, [CTL, "preset", "safe"]),
    (
        "export",
        {"session_id": 12, "format": "json"},
        [PY, BOT, "--export", f"{EXP}/goated-session-12.json", "--export-session-id", "12"],
    ),
    (
        "export",
        {"session_id": 3},
        [PY, BOT, "--export", f"{EXP}/goated-session-3.csv", "--export-session-id", "3"],
    ),
    ("cleanup", {}, [CTL, "cleanup"]),
    ("cleanup", {"days": 7}, [CTL, "cleanup", "7"]),
    (
        "backtest",
        {"simulate": 1000, "seed": 42},
        [PY, BOT, "--backtest", "--simulate", "1000", "--seed", "42"],
    ),
    (
        "backtest",
        {"replay_session": 12, "preset": "safe"},
        [PY, BOT, "--backtest", "--replay-session", "12", "--preset", "safe"],
    ),
    ("benchmark", {}, [PY, BOT, "--benchmark", "20", "--yes"]),
    ("benchmark", {"count": 5}, [PY, BOT, "--benchmark", "5", "--yes"]),
    ("diagnose", {}, [PY, BOT, "--diagnose"]),
    ("doctor", {}, [CTL, "doctor"]),
    ("doctor", {"fix": True}, [CTL, "doctor", "--fix", "--yes"]),
    ("update", {}, [CTL, "update"]),
    ("logs", {}, [JCTL, "--user", "-u", "goated", "-n", "200", "--no-pager"]),
    (
        "logs",
        {"service": "tg", "lines": 50},
        [JCTL, "--user", "-u", "goated-tg", "-n", "50", "--no-pager"],
    ),
    ("logs_follow", {}, [JCTL, "--user", "-u", "goated", "-n", "50", "-f", "--no-pager"]),
    ("start_all", {}, [CTL, "start-all"]),
    ("stop_all", {}, [CTL, "stop-all"]),
    ("status_all", {}, [CTL, "status-all"]),
    ("version", {}, [CTL, "version"]),
    *[(f"tg.{v}", {}, [CTL, "tg", v]) for v in ("start", "stop", "restart", "status")],
]


def test_every_command_has_an_argv_case() -> None:
    assert {name for name, _, _ in ARGV_CASES} == set(COMMANDS)


def test_goated_has_wolfbets_commands_but_the_live_signals() -> None:
    assert set(WOLFBET_COMMANDS) - set(COMMANDS) == {"pause", "resume", "reload"}
    assert set(COMMANDS) <= set(WOLFBET_COMMANDS)


@pytest.mark.parametrize(
    "name,args,argv", ARGV_CASES, ids=[f"{c[0]}-{i}" for i, c in enumerate(ARGV_CASES)]
)
async def test_argv(name: str, args: dict[str, Any], argv: list[str]) -> None:
    runner = GoatedRunner()
    await _service(runner).run_command(name, args)
    assert runner.calls[0][0] == argv
    assert runner.calls[0][2] == (PATHS.repo if name == "update" else None)


@pytest.mark.parametrize(
    "name,args",
    [
        ("status", {"lane": 0}),
        ("status", {"lane": 100}),
        ("lane_stop", {"lane": 100}),
        ("lane_remove", {"lane": 1}),
        ("lane_add", {"name": "-x"}),
        ("key_put", {"key_id": "../x", "label": "x", "secret": KEY_SECRET}),
        ("key_put", {"key_id": "a", "label": "x", "secret": "short"}),
        ("lanes", {"count": 2, "split": "half"}),
        ("export", {}),
        ("export", {"format": "json"}),
        ("export", {"session_id": 0}),
        ("benchmark", {"count": 5, "lanes": 2}),
        ("benchmark", {"count": 2}),
        ("backtest", {"sessions": 10}),
        ("backtest", {"simulate": 10, "replay_session": 1}),
        ("update", {"branch": "main"}),
        ("logs", {"service": "sshd"}),
        ("pause", {"target": "all"}),
        ("reload", {}),
    ],
)
async def test_bad_args_run_nothing(name: str, args: dict[str, Any]) -> None:
    runner = GoatedRunner()
    with pytest.raises(InvalidArgs):
        await _service(runner).run_command(name, args)
    assert runner.calls == []


@pytest.mark.parametrize("name", ["key_put", "key_rotate"])
async def test_a_key_reaches_the_bot_on_stdin_only(
    name: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    runner = GoatedRunner(RunResult(0, json.dumps({"v": 1, "key": {"id": "friend"}}), ""))
    args = {"key_id": "friend", "secret": KEY_SECRET}
    if name == "key_put":
        args["label"] = "Friend"
    service = _service(runner)
    data = await service.run_command(name, args)
    assert runner.stdins == [f"{KEY_SECRET}\n".encode()]
    assert KEY_SECRET not in " ".join(runner.calls[0][0])
    assert KEY_SECRET not in json.dumps(data) + caplog.text
    assert "secret" not in service.prepare(name, args).args


async def test_other_commands_get_no_stdin() -> None:
    runner = GoatedRunner()
    await _service(runner).run_command("key_list", {})
    assert runner.stdins == [None]


def test_long_running_commands_are_jobs_only() -> None:
    handlers = _service(GoatedRunner()).handlers()
    long_running = {f"goated.{n}" for n, s in COMMANDS.items() if s.long_running}
    assert long_running == {
        "goated.restart",
        "goated.lanes",
        "goated.export",
        "goated.backtest",
        "goated.benchmark",
        "goated.doctor",
        "goated.update",
        "goated.logs_follow",
    }
    assert not long_running & set(handlers)
    assert {
        "goated.status",
        "goated.config_set",
        "goated.config_dry_run",
        "goated.update_check",
    } <= set(handlers)
    assert not {"goated.pause", "goated.resume", "goated.reload"} & set(handlers)


def test_mutating_flags_follow_wolfbets() -> None:
    for name, spec in COMMANDS.items():
        assert spec.mutating == WOLFBET_COMMANDS[name].mutating, name


def test_reads_return_json_and_the_rest_text() -> None:
    texts = {n for n, s in COMMANDS.items() if not s.json_output}
    assert {"export", "backtest", "benchmark", "presets", "archives"} <= texts
    assert {
        "status",
        "config_show",
        "config_schema",
        "stats",
        "analytics",
        "last_bets",
        "session",
        "key_list",
        "key_put",
    }.isdisjoint(texts)


async def test_config_set_runs_the_dry_run_first_on_goateds_schema() -> None:
    runner = GoatedRunner(
        RunResult(0, json.dumps(SCHEMA), ""),
        RunResult(0, json.dumps({"v": 1, "base": []}), ""),
        RunResult(0, "Config updated", ""),
    )
    out = await _service(runner).config_set(
        {"pairs": {"maxloss": "0.01", "lane2.mult": "3", "condition": "below"}}
    )
    pairs = ["max_loss=0.01", "lane2.multiplier=3", "dice_condition=below"]
    assert [c[0] for c in runner.calls[1:]] == [
        [PY, BOT, "--dry-run", "--json", "--config-set", *pairs],
        [PY, BOT, "--config-set", *pairs],
    ]
    assert out["set"] == {"exit_code": 0, "stderr": "", "stdout": "Config updated"}


@pytest.mark.parametrize(
    ("key", "value"), [("rule", "over"), ("custom_rules_text", "x"), ("lane2.purge_days", "5")]
)
async def test_a_key_goated_does_not_take_is_refused_before_anything_runs(
    key: str, value: str
) -> None:
    runner = GoatedRunner()
    with pytest.raises(InvalidArgs):
        await _service(runner).config_dry_run({"pairs": {key: value}})
    assert [c[0][-1] for c in runner.calls] == ["--config-schema"]


def test_an_export_writes_into_its_own_directory() -> None:
    prepared = _service(GoatedRunner()).prepare("export", {"session_id": 12})
    assert prepared.file_id == FILE_ID
    assert prepared.argv[3] == f"{EXP}/goated-session-12.csv"


def test_paths_from_env(tmp_path: Path) -> None:
    paths = paths_from_env({"HOME": "/root", "STATE_DIRECTORY": "/var/lib/casino-agent"})
    assert paths.python == Path("/root/goated-bot/venv/bin/python3")
    assert paths.bot == Path("/root/goated-bot/goated.py")
    assert paths.ctl == Path("/root/.local/bin/goatedctl")
    assert paths.repo == Path("/root/goated")
    custom = paths_from_env({"HOME": "/root", "CASINO_AGENT_GOATED_CTL": "/opt/bin/goatedctl"})
    assert custom.ctl == Path("/opt/bin/goatedctl")
    with pytest.raises(ValueError, match="absolute: CASINO_AGENT_GOATED_INSTALL_DIR"):
        paths_from_env({"HOME": "/root", "CASINO_AGENT_GOATED_INSTALL_DIR": "goated-bot"})


def _ctl(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)


def test_the_agent_drives_wolfbet_and_goated(tmp_path: Path) -> None:
    _ctl(tmp_path / ".local/bin/wolfctl")
    _ctl(tmp_path / ".local/bin/goatedctl")
    env = {
        "HOME": str(tmp_path),
        "STATE_DIRECTORY": str(tmp_path / "s"),
        "CASINO_AGENT_SERVICES": "wolfbet,goated",
    }
    services = installed_services(env, JobManager(), None)
    assert [s.name for s in services] == ["wolfbet", "goated"]
    handlers = {name for s in services for name in s.handlers()}
    assert {"wolfbet.pause", "goated.status", "goated.key_put"} <= handlers
    assert "goated.pause" not in handlers


def test_a_missing_goatedctl_turns_only_goated_off(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _ctl(tmp_path / ".local/bin/wolfctl")
    caplog.set_level(logging.WARNING)
    env = {
        "HOME": str(tmp_path),
        "STATE_DIRECTORY": str(tmp_path / "s"),
        "CASINO_AGENT_SERVICES": "wolfbet,goated",
    }
    assert [s.name for s in installed_services(env, JobManager(), None)] == ["wolfbet"]
    assert any(
        "goated:" in r.getMessage() and "not found" in r.getMessage() for r in caplog.records
    )
