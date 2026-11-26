"""The agent drives every bot listed in CASINO_AGENT_SERVICES whose ctl is installed, each under
its own `<service>.*` commands; a missing bot only turns its own commands off."""

import logging
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from agent.cli import installed_services
from agent.config import ConfigError
from agent.files import FileStore
from agent.jobs import JobManager
from agent.runner import RunResult
from agent.services import registry
from agent.services.common import BotPaths, BotService, CommandSpec, NoArgs, _ctl_verb
from agent.services.registry import ServiceModule, configured
from agent.services.wolfbet import paths_from_env

DEMO_COMMANDS = {
    "status": CommandSpec(NoArgs, _ctl_verb("status"), 30),
    "start": CommandSpec(NoArgs, _ctl_verb("start"), 60, mutating=True),
}


def _demo_build(
    env: Mapping[str, str], busy: Callable[[], bool], files: FileStore | None
) -> BotService:
    paths = BotPaths.from_env(
        "demo", env, bot_file="demo.py", install_dir="demo-bot", repo_dir="demo", ctl_name="democtl"
    )
    return BotService(paths, busy=busy, files=files, name="demo", commands=DEMO_COMMANDS)


@pytest.fixture
def demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry.SERVICES, "demo", ServiceModule(_demo_build, "demo"))


def _ctl(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def _env(tmp_path: Path, services: str) -> dict[str, str]:
    return {
        "HOME": str(tmp_path),
        "STATE_DIRECTORY": str(tmp_path / "state"),
        "CASINO_AGENT_SERVICES": services,
    }


def test_wolfbet_is_the_default_and_a_repeat_counts_once() -> None:
    assert configured({}) == ["wolfbet"]
    assert configured({"CASINO_AGENT_SERVICES": "wolfbet, wolfbet"}) == ["wolfbet"]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("wolfbet,nosuch", "Unknown service in CASINO_AGENT_SERVICES: nosuch"),
        ("", "CASINO_AGENT_SERVICES needs at least one service"),
    ],
)
def test_a_bad_service_list_stops_the_agent(value: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        configured({"CASINO_AGENT_SERVICES": value})


def test_each_installed_bot_gets_its_own_commands(demo: None, tmp_path: Path) -> None:
    _ctl(tmp_path / ".local/bin/wolfctl")
    _ctl(tmp_path / ".local/bin/democtl")
    services = installed_services(_env(tmp_path, "wolfbet,demo"), JobManager(), None)
    assert [s.name for s in services] == ["wolfbet", "demo"]
    handlers = {name for s in services for name in s.handlers()}
    assert {"wolfbet.status", "demo.status", "demo.start", "demo.config_set"} <= handlers
    assert tuple(registry.SERVICES[s.name].unit for s in services) == ("wolfbet", "demo")


async def test_a_bot_command_runs_its_own_ctl_as_argv(demo: None, tmp_path: Path) -> None:
    ctl = _ctl(tmp_path / ".local/bin/democtl")
    calls: list[list[str]] = []

    async def runner(argv: list[str], timeout_s: float, **_: object) -> RunResult:
        calls.append(argv)
        return RunResult(0, "", "")

    service = _demo_build(_env(tmp_path, "demo"), lambda: False, None)
    service.runner = runner
    await service.handlers()["demo.status"]({})
    assert calls == [[str(ctl), "status"]]


def test_a_bot_whose_ctl_is_missing_only_turns_its_own_commands_off(
    demo: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _ctl(tmp_path / ".local/bin/wolfctl")
    caplog.set_level(logging.WARNING)
    services = installed_services(_env(tmp_path, "wolfbet,demo"), JobManager(), None)
    assert [s.name for s in services] == ["wolfbet"]
    assert any("demo:" in r.getMessage() and "not found" in r.getMessage() for r in caplog.records)


def test_a_relative_path_names_its_variable(tmp_path: Path) -> None:
    env = {**_env(tmp_path, "demo"), "CASINO_AGENT_DEMO_CTL": "democtl"}
    with pytest.raises(ValueError, match="absolute: CASINO_AGENT_DEMO_CTL"):
        _demo_build(env, lambda: False, None)


def test_wolfbet_still_reads_its_older_ctl_variable(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path), "CASINO_AGENT_WOLFBET_WOLFCTL": "/opt/wolfctl"}
    assert paths_from_env(env).ctl == Path("/opt/wolfctl")
    newer = {**env, "CASINO_AGENT_WOLFBET_CTL": "/srv/wolfctl"}
    assert paths_from_env(newer).ctl == Path("/srv/wolfctl")
