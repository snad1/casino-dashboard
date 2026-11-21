"""The socket only serves allowlisted peers sending signed, fresh, unique, small requests.

Real unix sockets under /tmp (macOS caps socket paths at 104 bytes); the peer
check uses the kernel's credentials for this test process.
"""

import asyncio
import grp
import json
import logging
import os
import shutil
import stat
import tempfile
import threading
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent import __version__
from agent.cli import main
from agent.config import AgentConfig
from agent.protocol import signed_request
from agent.server import AgentServer, Handler

TOKEN = b"Kq9vX2mLp7RtZ4wYb8NcFj3HdGs6Ae1U-test-token"
NOW = 1_800_000_000.0


@pytest.fixture
def sock_dir() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="ca-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _config(sock_dir: Path, **over: Any) -> AgentConfig:
    base = AgentConfig(
        socket_path=sock_dir / "agent.sock",
        token=TOKEN,
        allow_uids=frozenset({os.getuid()}),
        allow_gids=frozenset(),
    )
    return replace(base, **over)


async def _running(config: AgentConfig, handlers: dict[str, Handler] | None = None) -> AgentServer:
    server = AgentServer(config, handlers=handlers, clock=lambda: NOW)
    await server.start()
    return server


@pytest.fixture
async def agent(sock_dir: Path) -> AsyncIterator[AgentServer]:
    server = await _running(_config(sock_dir))
    yield server
    await server.close()


def _req(cmd: str = "ping", req_id: str = "r1", ts: float = NOW, **over: Any) -> bytes:
    msg = signed_request(TOKEN, req_id, cmd, int(ts * 1000))
    msg.update(over)
    return json.dumps(msg).encode() + b"\n"


async def _talk(server: AgentServer, *lines: bytes) -> list[dict[str, Any]]:
    """Send each line, collect a reply per line until the agent closes the connection."""
    reader, writer = await asyncio.open_unix_connection(str(server.config.socket_path))
    replies: list[dict[str, Any]] = []
    try:
        for line in lines:
            try:
                writer.write(line)
                await writer.drain()
                reply = await asyncio.wait_for(reader.readline(), 3)
            except ConnectionError:
                break
            if not reply:
                break
            replies.append(json.loads(reply))
    finally:
        writer.close()
    return replies


async def _closed(reader: asyncio.StreamReader) -> bool:
    return await asyncio.wait_for(reader.read(), 3) == b""


# ── allowed traffic ──
async def test_ping_and_version_on_one_connection(agent: AgentServer) -> None:
    replies = await _talk(agent, _req("ping", "a"), _req("version", "b"))
    assert replies == [
        {"id": "a", "ok": True, "data": {"pong": True}},
        {"id": "b", "ok": True, "data": {"agent": __version__, "proto": 1}},
    ]


async def test_peer_allowed_by_gid_only(sock_dir: Path) -> None:
    cfg = _config(sock_dir, allow_uids=frozenset(), allow_gids=frozenset({os.getgid()}))
    server = await _running(cfg)
    try:
        assert (await _talk(server, _req()))[0]["ok"] is True
    finally:
        await server.close()


async def test_socket_is_0660(agent: AgentServer) -> None:
    assert stat.S_IMODE(agent.config.socket_path.stat().st_mode) == 0o660


def _own_group() -> str:
    return grp.getgrgid(os.getgid()).gr_name


@pytest.fixture
def setgid_dir(sock_dir: Path) -> Path:
    os.chown(sock_dir, -1, os.getgid())
    sock_dir.chmod(0o2750)
    assert sock_dir.stat().st_mode & stat.S_ISGID
    return sock_dir


async def test_a_setgid_directory_gives_the_socket_its_group_without_chown(
    setgid_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(*_: Any) -> None:
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(os, "chown", refused)
    server = await _running(_config(setgid_dir, group=_own_group()))
    try:
        assert stat.S_IMODE(server.config.socket_path.stat().st_mode) == 0o660
        assert (await _talk(server, _req()))[0]["ok"] is True
    finally:
        await server.close()


async def test_without_setgid_the_socket_and_its_directory_are_given_the_group(
    sock_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, int, int]] = []
    monkeypatch.setattr(os, "chown", lambda p, u, g: calls.append((str(p), u, g)))
    server = await _running(_config(sock_dir, group=_own_group()))
    await server.close()
    gid = os.getgid()
    assert calls == [(str(sock_dir / "agent.sock"), -1, gid), (str(sock_dir), -1, gid)]


def test_the_unit_gives_the_runtime_directory_to_the_group_outside_the_sandbox() -> None:
    unit = (Path(__file__).resolve().parents[1] / "systemd" / "casino-agent.service").read_text()
    assert (
        'ExecStartPre=+/bin/sh -c \'chgrp casino-agent "%t/casino-agent" '
        '&& chmod 2750 "%t/casino-agent"\''
    ) in unit
    for line in ("ProtectSystem=strict", "PrivateTmp=yes", "NoNewPrivileges=yes"):
        assert line in unit.splitlines()


# ── refused traffic ──
async def test_peer_not_in_the_allowlist_is_refused_before_reading(sock_dir: Path) -> None:
    cfg = _config(sock_dir, allow_uids=frozenset({os.getuid() + 1}), allow_gids=frozenset({99999}))
    server = await _running(cfg)
    try:
        reader, writer = await asyncio.open_unix_connection(str(cfg.socket_path))
        assert json.loads(await reader.readline()) == {
            "id": None,
            "ok": False,
            "error": {"code": "unauthorized"},
        }
        assert await _closed(reader)
        writer.close()
    finally:
        await server.close()


@pytest.mark.parametrize(
    "line,code",
    [
        (_req(mac="0" * 64), "unauthorized"),
        (_req(ts=NOW - 31), "unauthorized"),
        (_req(ts=NOW + 31), "unauthorized"),
        (b"not json\n", "bad_request"),
        (b"[1, 2]\n", "bad_request"),
        (_req(cmd="status; reboot"), "bad_request"),
        (json.dumps({"proto": 1, "id": "x", "cmd": "ping"}).encode() + b"\n", "bad_request"),
    ],
)
async def test_bad_requests_get_a_generic_code_and_the_connection_closes(
    agent: AgentServer, line: bytes, code: str
) -> None:
    replies = await _talk(agent, line, _req("ping", "after"))
    assert len(replies) == 1 and replies[0]["ok"] is False
    assert replies[0]["error"] == {"code": code}
    assert set(replies[0]) == {"id", "ok", "error"}


async def test_tampered_args_break_the_mac(agent: AgentServer) -> None:
    msg = json.loads(_req())
    msg["args"] = {"lane": 2}
    replies = await _talk(agent, json.dumps(msg).encode() + b"\n")
    assert replies[0]["error"] == {"code": "unauthorized"}


async def test_inside_the_window_is_accepted(agent: AgentServer) -> None:
    assert (await _talk(agent, _req(ts=NOW - 29, req_id="old")))[0]["ok"] is True
    assert (await _talk(agent, _req(ts=NOW + 29, req_id="new")))[0]["ok"] is True


async def test_replayed_id_is_refused(agent: AgentServer) -> None:
    assert (await _talk(agent, _req(req_id="once")))[0]["ok"] is True
    assert (await _talk(agent, _req(req_id="once")))[0]["error"] == {"code": "replayed"}


async def test_seen_ids_expire_after_twice_the_window(agent: AgentServer) -> None:
    assert (await _talk(agent, _req(req_id="r")))[0]["ok"] is True
    later = NOW + 61
    agent.clock = lambda: later
    assert (await _talk(agent, _req(req_id="r", ts=later)))[0]["ok"] is True
    assert len(agent._seen) == 1


async def test_oversize_line_is_refused(agent: AgentServer) -> None:
    big = b'{"pad":"' + b"x" * (64 * 1024) + b'"}\n'
    replies = await _talk(agent, big)
    assert replies == [{"id": None, "ok": False, "error": {"code": "too_large"}}]


async def test_unknown_command_keeps_the_connection(agent: AgentServer) -> None:
    replies = await _talk(agent, _req("reboot", "u"), _req("ping", "p"))
    assert replies[0] == {"id": "u", "ok": False, "error": {"code": "unknown_command"}}
    assert replies[1]["ok"] is True


async def test_handler_failure_is_internal_and_logged_only(
    sock_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def boom(_args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("secret detail /etc/shadow")

    server = await _running(_config(sock_dir), handlers={"ping": boom})
    try:
        with caplog.at_level(logging.INFO, logger="casino-agent"):
            replies = await _talk(server, _req())
    finally:
        await server.close()
    assert replies == [{"id": "r1", "ok": False, "error": {"code": "internal"}}]
    assert "secret detail" in caplog.text
    assert "secret detail" not in json.dumps(replies)


async def test_idle_connection_is_closed(sock_dir: Path) -> None:
    server = await _running(_config(sock_dir, idle_timeout=0.2))
    try:
        reader, writer = await asyncio.open_unix_connection(str(server.config.socket_path))
        assert await _closed(reader)
        writer.close()
    finally:
        await server.close()


async def test_connections_per_peer_are_capped(sock_dir: Path) -> None:
    server = await _running(_config(sock_dir, max_conns_per_peer=2))
    path = str(server.config.socket_path)
    try:
        held = [await asyncio.open_unix_connection(path) for _ in range(2)]
        await asyncio.sleep(0.05)
        reader, writer = await asyncio.open_unix_connection(path)
        assert json.loads(await reader.readline())["error"] == {"code": "busy"}
        writer.close()
        for _, w in held:
            w.close()
        await asyncio.sleep(0.05)
        assert (await _talk(server, _req()))[0]["ok"] is True
    finally:
        await server.close()


async def test_logs_never_hold_the_token_or_mac(
    agent: AgentServer, caplog: pytest.LogCaptureFixture
) -> None:
    line = _req(mac="0" * 64)
    with caplog.at_level(logging.DEBUG, logger="casino-agent"):
        await _talk(agent, _req(req_id="ok1"), line, b"junk\n")
    good_mac = json.loads(_req(req_id="ok1"))["mac"]
    assert TOKEN.decode() not in caplog.text and good_mac not in caplog.text


async def test_refuses_to_replace_a_file_that_is_not_a_socket(sock_dir: Path) -> None:
    (sock_dir / "agent.sock").write_text("keep")
    with pytest.raises(RuntimeError, match="is not a socket"):
        await AgentServer(_config(sock_dir)).start()
    assert (sock_dir / "agent.sock").read_text() == "keep"


# ── casino-agent ping ──
def test_cli_ping_end_to_end(
    sock_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file = sock_dir / "token"
    token_file.write_bytes(TOKEN)
    token_file.chmod(0o600)
    monkeypatch.setenv("CASINO_AGENT_SOCKET", str(sock_dir / "agent.sock"))
    monkeypatch.setenv("CASINO_AGENT_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("CASINO_AGENT_ALLOW_UIDS", str(os.getuid()))
    loop = asyncio.new_event_loop()
    server = AgentServer(_config(sock_dir))
    loop.run_until_complete(server.start())
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        assert main(["ping"]) == 0
        assert capsys.readouterr().out == f"pong (proto 1, agent {__version__})\n"
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.run_until_complete(server.close())
        loop.close()


def test_cli_ping_without_an_agent_exits_1(
    sock_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file = sock_dir / "token"
    token_file.write_bytes(TOKEN)
    token_file.chmod(0o600)
    monkeypatch.setenv("CASINO_AGENT_SOCKET", str(sock_dir / "none.sock"))
    monkeypatch.setenv("CASINO_AGENT_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("CASINO_AGENT_ALLOW_UIDS", str(os.getuid()))
    assert main(["ping"]) == 1
    assert "cannot reach the agent" in capsys.readouterr().err
