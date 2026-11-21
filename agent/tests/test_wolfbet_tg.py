"""The Telegram bot's commands: typed args, a signed request the bot can verify, mapped answers.

A small unix-socket server stands in for the TG bot: it checks the mac with this agent's own
protocol code and answers with a canned reply line.
"""

import asyncio
import json
import os
import shutil
import tempfile
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from agent.errors import Busy, NotFound
from agent.protocol import Request, verify_mac
from agent.runner import CommandTimeout
from agent.services.wolfbet_tg import COMMANDS, WolfBetTgPaths, WolfBetTgService
from agent.validators import InvalidArgs

TOKEN = "tg-control-" + "0123456789abcdefXYZ" * 2


@pytest.fixture
def tg_dir() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="tga", dir="/tmp"))
    token = path / "control.token"
    token.write_text(TOKEN + "\n")
    os.chmod(token, 0o600)
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _service(tg_dir: Path) -> WolfBetTgService:
    return WolfBetTgService(WolfBetTgPaths(tg_dir / "control.sock", tg_dir / "control.token"))


async def _with_bot(
    tg_dir: Path,
    reply: dict[str, Any] | None,
    call: Callable[[], Awaitable[dict[str, Any]]],
    delay: float = 0.0,
) -> tuple[Any, list[dict[str, Any]]]:
    """Run `call` against a fake TG bot; return (call result or exception, requests seen)."""
    seen: list[dict[str, Any]] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        raw = json.loads(await reader.readline())
        seen.append(raw)
        assert verify_mac(TOKEN.encode(), Request.model_validate(raw))
        await asyncio.sleep(delay)
        if reply is not None:
            writer.write(json.dumps({"id": raw["id"], **reply}).encode() + b"\n")
            await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(handle, path=str(tg_dir / "control.sock"))
    try:
        try:
            result: Any = await call()
        except Exception as e:  # the test inspects which one
            result = e
    finally:
        server.close()
        await server.wait_closed()
    return result, seen


def test_every_tg_command_is_registered(tg_dir: Path) -> None:
    assert set(_service(tg_dir).handlers()) == {f"wolfbet_tg.{name}" for name in COMMANDS}


def test_a_command_is_signed_and_its_data_returned(tg_dir: Path) -> None:
    svc = _service(tg_dir)
    result, seen = asyncio.run(
        _with_bot(
            tg_dir,
            {"ok": True, "data": {"slots": [2]}},
            lambda: svc.handlers()["wolfbet_tg.pause"]({"uid": 42, "slot": 2}),
        )
    )
    assert result == {"slots": [2]}
    assert seen[0]["cmd"] == "pause"
    assert seen[0]["args"] == {"uid": 42, "slot": 2}


def test_an_unset_slot_is_not_sent(tg_dir: Path) -> None:
    svc = _service(tg_dir)
    _, seen = asyncio.run(
        _with_bot(
            tg_dir, {"ok": True, "data": {"slots": [1]}}, lambda: svc.call("stop", {"uid": 7})
        )
    )
    assert seen[0]["args"] == {"uid": 7}


@pytest.mark.parametrize(
    "cmd,args",
    [
        ("pause", {}),
        ("pause", {"uid": 0}),
        ("pause", {"uid": "42"}),
        ("pause", {"uid": True}),
        ("pause", {"uid": 1, "slot": -1}),
        ("status", {"uid": 1, "admin": True}),
        ("set", {"uid": 1, "key": "Max-Loss", "value": "1"}),
        ("set", {"uid": 1, "key": "maxloss", "value": ""}),
        ("set", {"uid": 1, "key": "maxloss", "value": "x" * 257}),
    ],
)
def test_bad_args_never_reach_the_bot(tg_dir: Path, cmd: str, args: dict[str, Any]) -> None:
    result, seen = asyncio.run(
        _with_bot(tg_dir, {"ok": True, "data": {}}, lambda: _service(tg_dir).call(cmd, args))
    )
    assert isinstance(result, InvalidArgs)
    assert seen == []


@pytest.mark.parametrize(
    "code,error",
    [
        ("bad_request", InvalidArgs),
        ("not_found", NotFound),
        ("busy", Busy),
        ("internal", RuntimeError),
    ],
)
def test_bot_codes_map_to_agent_errors(tg_dir: Path, code: str, error: type) -> None:
    result, _ = asyncio.run(
        _with_bot(
            tg_dir,
            {"ok": False, "error": {"code": code}},
            lambda: _service(tg_dir).call("status", {"uid": 1}),
        )
    )
    assert type(result) is error


def test_no_socket_means_busy(tg_dir: Path) -> None:
    with pytest.raises(Busy):
        asyncio.run(_service(tg_dir).call("status", {"uid": 1}))


def test_a_bot_that_closes_without_replying_is_busy(tg_dir: Path) -> None:
    result, _ = asyncio.run(
        _with_bot(tg_dir, None, lambda: _service(tg_dir).call("status", {"uid": 1}))
    )
    assert isinstance(result, Busy)


def test_a_silent_bot_times_out(tg_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.services.wolfbet_tg.TIMEOUT_S", 0.2)
    result, _ = asyncio.run(
        _with_bot(
            tg_dir,
            {"ok": True, "data": {}},
            lambda: _service(tg_dir).call("status", {"uid": 1}),
            delay=1.0,
        )
    )
    assert isinstance(result, CommandTimeout)


def test_a_token_others_can_read_is_not_used(tg_dir: Path) -> None:
    os.chmod(tg_dir / "control.token", 0o644)
    result, seen = asyncio.run(
        _with_bot(
            tg_dir, {"ok": True, "data": {}}, lambda: _service(tg_dir).call("status", {"uid": 1})
        )
    )
    assert isinstance(result, Busy)
    assert TOKEN not in str(result)
    assert seen == []


def test_paths_default_to_the_bots_data_dir() -> None:
    paths = WolfBetTgPaths.from_env({"HOME": "/home/bot"})
    assert paths.socket == Path("/home/bot/.wolfbot_tg/control.sock")
    assert paths.token_file == Path("/home/bot/.wolfbot_tg/control.token")
    with pytest.raises(ValueError):
        WolfBetTgPaths.from_env({"HOME": "/home/bot", "CASINO_AGENT_WOLFBET_TG_SOCKET": "rel.sock"})


def test_config_reads_one_users_settings(tg_dir: Path) -> None:
    svc = _service(tg_dir)
    result, seen = asyncio.run(
        _with_bot(
            tg_dir,
            {"ok": True, "data": {"config": {"currency": "btc"}}},
            lambda: svc.handlers()["wolfbet_tg.config"]({"uid": 42}),
        )
    )
    assert result == {"config": {"currency": "btc"}}
    assert seen[0]["cmd"] == "config"
    assert seen[0]["args"] == {"uid": 42}
