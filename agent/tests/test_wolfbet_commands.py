"""WolfBet's allowlist: the exact argv per command, the config flow, the runner's limits.

A fake runner records argv for the table tests; the runner tests start real tiny
scripts. The config schema fixture is `wolfbet.py --config-schema` from CLI 2.44.0.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agent.config import AgentConfig
from agent.files import FileStore
from agent.protocol import signed_request
from agent.runner import CommandTimeout, RunResult, run
from agent.server import DEFAULT_HANDLERS, AgentServer
from agent.services.wolfbet import COMMANDS, WolfBetPaths, WolfBetService, config_pair
from agent.validators import InvalidArgs

FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA = json.loads((FIXTURES / "wolfbet-config-schema.json").read_text())
PATHS = WolfBetPaths(
    python=Path("/opt/wb/venv/bin/python3"),
    bot=Path("/opt/wb/wolfbet.py"),
    wolfctl=Path("/opt/bin/wolfctl"),
    repo=Path("/opt/repo"),
    export_dir=Path("/var/agent/exports"),
    journalctl=Path("/usr/bin/journalctl"),
)
FILE_ID = "f" * 32
EXP = f"/var/agent/exports/{FILE_ID}"


class StubFiles(FileStore):
    """Hands out one fixed export directory so the export argv is predictable."""

    def __init__(self) -> None:
        super().__init__(Path(EXP).parent)

    def allocate(self) -> tuple[str, Path]:
        return FILE_ID, Path(EXP)


PY, BOT, CTL, JCTL = (
    "/opt/wb/venv/bin/python3",
    "/opt/wb/wolfbet.py",
    "/opt/bin/wolfctl",
    "/usr/bin/journalctl",
)


class FakeRunner:
    """Records each call and answers from a queue of RunResults (default: exit 0, `{}`)."""

    def __init__(self, *results: RunResult) -> None:
        self.calls: list[tuple[list[str], float, Path | None]] = []
        self.results = list(results)

    async def __call__(
        self, argv: list[str], timeout_s: float, cwd: Path | None = None
    ) -> RunResult:
        self.calls.append((list(argv), timeout_s, cwd))
        if self.results:
            return self.results.pop(0)
        if argv[-1] == "--config-schema":
            return RunResult(0, json.dumps(SCHEMA), "")
        return RunResult(0, "{}", "")


def _ok(doc: dict[str, Any]) -> RunResult:
    return RunResult(0, json.dumps(doc), "")


ARGV_CASES: list[tuple[str, dict[str, Any], list[str]]] = [
    ("start", {}, [CTL, "start"]),
    ("stop", {}, [CTL, "stop"]),
    ("restart", {}, [CTL, "restart"]),
    ("status", {}, [PY, BOT, "--status", "--json"]),
    ("status", {"lane": 3}, [PY, BOT, "--status", "--json", "--lane", "3"]),
    ("pause", {}, [CTL, "pause", "all"]),
    ("pause", {"target": 2}, [CTL, "pause", "2"]),
    ("resume", {"target": "all"}, [CTL, "resume", "all"]),
    ("reload", {"target": 10}, [CTL, "reload", "10"]),
    ("lanes", {"count": 3}, [CTL, "lanes", "3", "--yes"]),
    ("lanes", {"count": 2, "split": "budget"}, [CTL, "lanes", "2", "--yes", "--split", "budget"]),
    ("config_show", {}, [PY, BOT, "--config-show", "--json"]),
    ("config_schema", {}, [PY, BOT, "--config-schema"]),
    ("run_reset", {}, [CTL, "run-reset", "--yes"]),
    ("reset", {}, [CTL, "reset", "--yes"]),
    ("archives", {}, [CTL, "archives"]),
    ("stats", {}, [PY, BOT, "--stats", "--json"]),
    (
        "stats",
        {"since": "2026-11-01", "until": "2026-11-02 14:00", "lane": 2},
        [
            PY,
            BOT,
            "--stats",
            "--json",
            "--since",
            "2026-11-01",
            "--until",
            "2026-11-02T14:00:00",
            "--lane",
            "2",
        ],
    ),
    (
        "analytics",
        {"since": "2026-11-01T08:30:05"},
        [PY, BOT, "--analytics", "--json", "--since", "2026-11-01T08:30:05"],
    ),
    ("last_bets", {}, [PY, BOT, "--last-bets", "20", "--json"]),
    ("last_bets", {"count": 500}, [PY, BOT, "--last-bets", "500", "--json"]),
    ("session", {"session_id": 42}, [PY, BOT, "--session-bets", "42", "--json"]),
    ("presets", {}, [PY, BOT, "--list-presets"]),
    ("preset", {"name": "martingale-safe"}, [CTL, "preset", "martingale-safe"]),
    ("export", {}, [PY, BOT, "--export", f"{EXP}/", "--json"]),
    (
        "export",
        {"session_id": 7},
        [PY, BOT, "--export", f"{EXP}/", "--export-session-id", "7", "--json"],
    ),
    (
        "export",
        {"session_id": 7, "format": "json"},
        [
            PY,
            BOT,
            "--export",
            f"{EXP}/wolfbet-session-7.json",
            "--export-session-id",
            "7",
            "--json",
        ],
    ),
    ("cleanup", {}, [CTL, "cleanup", "--yes"]),
    ("cleanup", {"days": 7}, [CTL, "cleanup", "--yes", "7"]),
    (
        "backtest",
        {"simulate": 1000, "seed": 42, "sessions": 50, "runs": 3, "balance": 0.5, "preset": "p1"},
        [
            PY,
            BOT,
            "--backtest",
            "--json",
            "--simulate",
            "1000",
            "--seed",
            "42",
            "--sessions",
            "50",
            "--runs",
            "3",
            "--balance",
            "0.5",
            "--preset",
            "p1",
        ],
    ),
    ("backtest", {"replay_session": 9}, [PY, BOT, "--backtest", "--json", "--replay-session", "9"]),
    ("benchmark", {}, [PY, BOT, "--benchmark", "20"]),
    ("benchmark", {"count": 50, "lanes": 3}, [PY, BOT, "--benchmark", "50", "--lanes", "3"]),
    ("diagnose", {}, [PY, BOT, "--diagnose"]),
    ("doctor", {}, [CTL, "doctor"]),
    ("doctor", {"fix": True}, [CTL, "doctor", "--fix", "--yes"]),
    ("update", {}, [CTL, "update", "--yes"]),
    (
        "update",
        {"branch": "feat/casino-dashboard"},
        [CTL, "update", "--yes", "--branch", "feat/casino-dashboard"],
    ),
    ("logs", {}, [JCTL, "--user", "-u", "wolfbet", "-n", "200", "--no-pager"]),
    (
        "logs",
        {"service": "tg", "lines": 50},
        [JCTL, "--user", "-u", "wolfbet-tg", "-n", "50", "--no-pager"],
    ),
    (
        "logs_follow",
        {"service": "tg"},
        [JCTL, "--user", "-u", "wolfbet-tg", "-n", "50", "-f", "--no-pager"],
    ),
    ("start_all", {}, [CTL, "start-all"]),
    ("stop_all", {}, [CTL, "stop-all"]),
    ("status_all", {}, [CTL, "status-all"]),
    ("version", {}, [CTL, "version"]),
    *[(f"tg.{v}", {}, [CTL, "tg", v]) for v in ("start", "stop", "restart", "status")],
]


def test_every_command_has_an_argv_case() -> None:
    assert {name for name, _, _ in ARGV_CASES} == set(COMMANDS)


@pytest.mark.parametrize(
    "name,args,argv", ARGV_CASES, ids=[f"{c[0]}-{i}" for i, c in enumerate(ARGV_CASES)]
)
async def test_argv(name: str, args: dict[str, Any], argv: list[str]) -> None:
    runner = FakeRunner()
    await WolfBetService(PATHS, runner, files=StubFiles()).run_command(name, args)
    assert runner.calls[0][0] == argv
    assert runner.calls[0][2] == (PATHS.repo if name == "update" else None)


@pytest.mark.parametrize(
    "name,args",
    [
        ("status", {"lane": 0}),
        ("status", {"lane": 11}),
        ("status", {"lane": "2"}),
        ("pause", {"target": "1;id"}),
        ("lanes", {"count": 2, "split": "half"}),
        ("stats", {"since": "yesterday"}),
        ("stats", {"since": "2026-13-01"}),
        ("stats", {"until": "2026-11-01 25:00"}),
        ("last_bets", {"count": 0}),
        ("session", {}),
        ("preset", {"name": "../x"}),
        ("preset", {"name": "-rf"}),
        ("export", {"format": "json"}),
        ("export", {"path": "/etc/passwd"}),
        ("cleanup", {"days": 31}),
        ("backtest", {"simulate": 10, "replay_session": 1}),
        ("backtest", {"balance": float("nan")}),
        ("backtest", {"balance": -1.0}),
        ("benchmark", {"count": 2}),
        ("update", {"branch": "a..b"}),
        ("update", {"branch": "-x"}),
        ("logs", {"service": "sshd"}),
        ("logs", {"lines": 5000}),
        ("start", {"extra": 1}),
    ],
)
async def test_bad_args_run_nothing(name: str, args: dict[str, Any]) -> None:
    runner = FakeRunner()
    with pytest.raises(InvalidArgs):
        await WolfBetService(PATHS, runner).run_command(name, args)
    assert runner.calls == []


def test_long_running_commands_are_not_served_yet() -> None:
    handlers = WolfBetService(PATHS, FakeRunner()).handlers()
    long_running = {f"wolfbet.{n}" for n, s in COMMANDS.items() if s.long_running}
    assert long_running == {
        "wolfbet.restart",
        "wolfbet.lanes",
        "wolfbet.export",
        "wolfbet.backtest",
        "wolfbet.benchmark",
        "wolfbet.doctor",
        "wolfbet.update",
        "wolfbet.logs_follow",
    }
    assert not long_running & set(handlers)
    assert {"wolfbet.status", "wolfbet.config_set", "wolfbet.config_dry_run"} <= set(handlers)


def test_mutating_flags() -> None:
    reads = {n for n, s in COMMANDS.items() if not s.mutating}
    assert reads == {
        "status",
        "config_show",
        "config_schema",
        "archives",
        "stats",
        "analytics",
        "last_bets",
        "session",
        "presets",
        "export",
        "backtest",
        "benchmark",
        "diagnose",
        "logs",
        "logs_follow",
        "status_all",
        "tg.status",
        "version",
    }


async def test_json_and_text_results() -> None:
    runner = FakeRunner(
        _ok({"v": 1, "running": True}),
        RunResult(0, "plain text\n", "warn"),
        RunResult(1, "not json", "boom"),
    )
    service = WolfBetService(PATHS, runner)
    assert await service.run_command("status", {}) == {
        "exit_code": 0,
        "stderr": "",
        "json": {"v": 1, "running": True},
    }
    assert await service.run_command("archives", {}) == {
        "exit_code": 0,
        "stderr": "warn",
        "stdout": "plain text\n",
    }
    assert await service.run_command("stats", {}) == {
        "exit_code": 1,
        "stderr": "boom",
        "stdout": "not json",
    }


# ── config keys and values against the schema ──
@pytest.mark.parametrize(
    "key,value,item",
    [
        ("maxloss", "0.01", "max_loss=0.01"),
        ("max_loss", "off", "max_loss=off"),
        ("recdelay", "99999", "recurring_delay=99999"),
        ("recurring", "true", "recurring=on"),
        ("events_enabled", "OFF", "events_enabled=off"),
        ("strategy", "2", "strategy_key=2"),
        ("game", "dice-auto", "game=dice-auto"),
        ("sdloss", "3:5", "streak_delay_loss=3:5"),
        ("maxbets", "1000", "max_bets=1000"),
        ("proxy", "http://user:pw@10.0.0.1:8080", "proxy=http://user:pw@10.0.0.1:8080"),
        ("events_url", "unix:///run/redis.sock", "events_url=unix:///run/redis.sock"),
        ("lanes", "4", "lanes=4"),
        ("lane_split", "both", "lane_split=both"),
        ("lane2.multiplier", "3", "lane2.multiplier=3"),
        ("LANE3.maxloss", "0.002", "lane3.max_loss=0.002"),
        ("lane2.multiplier", "default", "lane2.multiplier=default"),
    ],
)
def test_config_pairs_accepted(key: str, value: str, item: str) -> None:
    assert config_pair(SCHEMA, key, value) == item


@pytest.mark.parametrize(
    "key,value",
    [
        ("api_key", "abc"),
        ("custom_rules_text", "x"),
        ("nope", "1"),
        ("maxloss", "1;id"),
        ("maxloss", "nan"),
        ("maxloss", "1e999"),
        ("maxbets", "1.5"),
        ("game", "roulette"),
        ("recurring", "maybe"),
        ("sdloss", "3"),
        ("streak_reset_losses", "0"),
        ("proxy", "ftp://h"),
        ("proxy", "http://h x"),
        ("events_url", "redis://"),
        ("lanes", "11"),
        ("lane_split", "half"),
        ("lane1.multiplier", "3"),
        ("lane11.maxloss", "1"),
        ("lane2.proxy", "http://h"),
        ("lanex.maxloss", "1"),
        ("maxloss", "0.01\n--json"),
        ("hybrid_recover_bet", "0.5"),
    ],
)
def test_config_pairs_refused(key: str, value: str) -> None:
    with pytest.raises(InvalidArgs):
        config_pair(SCHEMA, key, value)


async def test_config_set_runs_the_dry_run_first_then_the_set() -> None:
    runner = FakeRunner(
        RunResult(0, json.dumps(SCHEMA), ""),
        _ok({"v": 1, "base": []}),
        RunResult(0, "Config updated", ""),
    )
    out = await WolfBetService(PATHS, runner).config_set(
        {"pairs": {"maxloss": "0.01", "lane2.multiplier": "3"}}
    )
    pairs = ["max_loss=0.01", "lane2.multiplier=3"]
    assert [c[0] for c in runner.calls[1:]] == [
        [PY, BOT, "--dry-run", "--json", "--config-set", *pairs],
        [PY, BOT, "--config-set", *pairs],
    ]
    assert out["dry_run"]["json"] == {"v": 1, "base": []}
    assert out["set"] == {"exit_code": 0, "stderr": "", "stdout": "Config updated"}


async def test_failed_dry_run_stops_before_the_set() -> None:
    runner = FakeRunner(
        RunResult(0, json.dumps(SCHEMA), ""),
        RunResult(2, json.dumps({"v": 1, "error": {"code": "invalid_value"}}), ""),
    )
    out = await WolfBetService(PATHS, runner).config_set({"pairs": {"maxloss": "0.01"}})
    assert out["set"] is None and out["dry_run"]["exit_code"] == 2
    assert len(runner.calls) == 2


async def test_config_schema_is_cached_and_update_clears_it() -> None:
    runner = FakeRunner()
    service = WolfBetService(PATHS, runner)
    await service.config_dry_run({"pairs": {"maxloss": "0.01"}})
    await service.config_dry_run({"pairs": {"maxwins": "5"}})
    schema_calls = [c for c in runner.calls if c[0][-1] == "--config-schema"]
    assert len(schema_calls) == 1
    await service.run_command("update", {})
    await service.config_dry_run({"pairs": {"maxwins": "6"}})
    assert len([c for c in runner.calls if c[0][-1] == "--config-schema"]) == 2


@pytest.mark.parametrize(
    "args", [{"pairs": {}}, {"pairs": {"k": 1}}, {}, {"pairs": {f"k{i}": "1" for i in range(33)}}]
)
async def test_config_args_shape(args: dict[str, Any]) -> None:
    runner = FakeRunner()
    with pytest.raises(InvalidArgs):
        await WolfBetService(PATHS, runner).config_dry_run(args)
    assert runner.calls == []


def test_paths_from_env_are_absolute(tmp_path: Path) -> None:
    paths = WolfBetPaths.from_env({"HOME": "/root", "STATE_DIRECTORY": "/var/lib/casino-agent"})
    assert paths.python == Path("/root/wolfbet-bot/venv/bin/python3")
    assert paths.wolfctl == Path("/root/.local/bin/wolfctl")
    assert paths.export_dir == Path("/var/lib/casino-agent/exports")
    with pytest.raises(ValueError, match="absolute"):
        WolfBetPaths.from_env({"HOME": "/root", "CASINO_AGENT_WOLFBET_WOLFCTL": "wolfctl"})


# ── the runner ──
@pytest.fixture
def bin_dir() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="cab-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _script(bin_dir: Path, name: str, body: str) -> str:
    path = bin_dir / name
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    path.chmod(0o755)
    return str(path)


async def test_runner_scrubs_the_environment(bin_dir: Path) -> None:
    exe = _script(
        bin_dir,
        "env.py",
        """
        import json, os, sys
        print(json.dumps(dict(os.environ)))
        print(sys.stdin.read() == "", file=sys.stderr)
    """,
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/root",
        "LANG": "C.UTF-8",
        "XDG_RUNTIME_DIR": "/run/user/0",
        "AGENT_TOKEN": "s3cret",
        "WOLFBET_API_KEY": "k",
    }
    result = await run([exe], 10, env=env)
    seen = json.loads(result.stdout)
    assert "AGENT_TOKEN" not in seen and "WOLFBET_API_KEY" not in seen
    assert {k: seen[k] for k in ("PATH", "HOME", "LANG", "XDG_RUNTIME_DIR")} == {
        k: env[k] for k in ("PATH", "HOME", "LANG", "XDG_RUNTIME_DIR")
    }
    assert result.stderr.strip() == "True"


async def test_runner_timeout_kills_the_process_group(bin_dir: Path) -> None:
    marker = bin_dir / "child.pid"
    exe = _script(
        bin_dir,
        "slow.py",
        f"""
        import subprocess, time
        child = subprocess.Popen(["sleep", "30"])
        open({str(marker)!r}, "w").write(str(child.pid))
        time.sleep(30)
    """,
    )
    with pytest.raises(CommandTimeout):
        await run([exe], 2.0)
    child = int(marker.read_text())
    await asyncio.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


async def test_runner_returns_the_exit_code_and_refuses_relative_argv(bin_dir: Path) -> None:
    exe = _script(bin_dir, "fail.py", "import sys; print('out'); sys.exit(3)\n")
    assert await run([exe], 10) == RunResult(3, "out\n", "")
    with pytest.raises(ValueError, match="absolute"):
        await run(["python3", "-c", "1"], 10)


# ── through the socket ──
TOKEN = b"Kq9vX2mLp7RtZ4wYb8NcFj3HdGs6Ae1U-test-token"


async def _ask(server: AgentServer, cmd: str, args: dict[str, Any], req_id: str) -> Any:
    reader, writer = await asyncio.open_unix_connection(str(server.config.socket_path))
    msg = signed_request(TOKEN, req_id, cmd, int(server.clock() * 1000), args)
    writer.write(json.dumps(msg).encode() + b"\n")
    await writer.drain()
    reply = json.loads(await asyncio.wait_for(reader.readline(), 3))
    writer.close()
    return reply


async def test_commands_through_the_agent(bin_dir: Path) -> None:
    runner = FakeRunner(_ok({"v": 1, "lifetime": {}}))
    service = WolfBetService(PATHS, runner)
    config = AgentConfig(
        socket_path=bin_dir / "a.sock",
        token=TOKEN,
        allow_uids=frozenset({os.getuid()}),
        allow_gids=frozenset(),
    )
    server = AgentServer(config, handlers={**DEFAULT_HANDLERS, **service.handlers()})
    await server.start()
    try:
        ok = await _ask(server, "wolfbet.analytics", {"lane": 2}, "a1")
        assert ok == {
            "id": "a1",
            "ok": True,
            "data": {"exit_code": 0, "stderr": "", "json": {"v": 1, "lifetime": {}}},
        }
        assert runner.calls[-1][0] == [PY, BOT, "--analytics", "--json", "--lane", "2"]
        bad = await _ask(server, "wolfbet.analytics", {"lane": "2; rm -rf /"}, "a2")
        assert bad == {"id": "a2", "ok": False, "error": {"code": "bad_request"}}
        later = await _ask(server, "wolfbet.update", {}, "a3")
        assert later["error"] == {"code": "unknown_command"}
        assert len(runner.calls) == 1

        async def too_slow(argv: list[str], timeout_s: float, cwd: Path | None = None) -> RunResult:
            raise CommandTimeout("wolfbet.py ran over 30 s")

        service.runner = too_slow
        slow = await _ask(server, "wolfbet.status", {}, "a4")
        assert slow == {"id": "a4", "ok": False, "error": {"code": "timeout"}}
    finally:
        await server.close()


async def test_a_refused_config_value_names_its_key_and_nothing_else(bin_dir: Path) -> None:
    service = WolfBetService(PATHS, FakeRunner())
    config = AgentConfig(
        socket_path=bin_dir / "a.sock",
        token=TOKEN,
        allow_uids=frozenset({os.getuid()}),
        allow_gids=frozenset(),
    )
    server = AgentServer(config, handlers=service.handlers())
    await server.start()
    try:
        proxy = await _ask(server, "wolfbet.config_dry_run", {"pairs": {"proxy": "direct"}}, "c1")
        assert proxy["error"] == {"code": "bad_request", "key": "proxy"}
        alias = await _ask(server, "wolfbet.config_dry_run", {"pairs": {"maxloss": "x"}}, "c2")
        assert alias["error"] == {"code": "bad_request", "key": "max_loss"}
        unknown = await _ask(server, "wolfbet.config_dry_run", {"pairs": {"nope": "1"}}, "c3")
        assert unknown["error"] == {"code": "bad_request"}
    finally:
        await server.close()
