"""Terminal sessions: echo round trip, resize, timers, shell exit, recording, limits, audit.

`/bin/sh` stands in for tmux + zsh so the tests run on any host.
"""

import asyncio
import base64
import json
import os
import shutil
import stat
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from agent.config import AgentConfig
from agent.errors import Busy, NotFound
from agent.jobs import JobManager
from agent.protocol import Chunk, Streaming, signed_request
from agent.pty import (
    FRAME_BYTES,
    TERM,
    TMUX_SESSION,
    Launch,
    PtyManager,
    Session,
    default_launch,
    terminal_env,
)
from agent.server import DEFAULT_HANDLERS, AgentServer
from agent.validators import InvalidArgs

SH = Launch(["/bin/sh"], "/bin/sh")
TOKEN = b"Kq9vX2mLp7RtZ4wYb8NcFj3HdGs6Ae1U-test-token"


@pytest.fixture
def work() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="cap-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


class AuditLog(list[dict[str, Any]]):
    def __call__(self, event: str, **fields: Any) -> None:
        self.append({"event": event, **fields})


def _manager(work: Path, audit: Any = None, **kw: Any) -> PtyManager:
    kw.setdefault("hangup_grace_s", 0.5)
    return PtyManager(work / "recordings", audit or AuditLog(), launch=lambda: SH, **kw)


@pytest.fixture
async def pty(work: Path) -> AsyncIterator[PtyManager]:
    manager = _manager(work)
    yield manager
    await manager.shutdown()


async def _read_until(m: PtyManager, s: Session, needle: bytes) -> bytes:
    seen = b""

    async def follow() -> None:
        nonlocal seen
        async for frame in m.stream(s.id):
            seen += base64.b64decode(frame["data"])
            if needle in seen:
                return

    await asyncio.wait_for(follow(), 10)
    return seen


def _events(path: Path) -> tuple[dict[str, Any], list[list[Any]]]:
    lines = path.read_text().splitlines()
    return json.loads(lines[0]), [json.loads(line) for line in lines[1:]]


# ── session ──
async def test_echo_round_trip(pty: PtyManager) -> None:
    s = pty.open(80, 24)
    await pty.write(s.id, b"echo $((40+2))\n")
    assert b"42" in await _read_until(pty, s, b"42")


async def test_resize_reaches_the_shell_and_the_recording(pty: PtyManager, work: Path) -> None:
    s = pty.open(80, 24)
    pty.resize(s.id, 100, 40)
    await pty.write(s.id, b"stty size\n")
    await _read_until(pty, s, b"40 100")
    await pty.close(s.id)
    _, events = _events(work / "recordings" / f"{s.id}.cast")
    assert [e[2] for e in events if e[1] == "r"] == ["100x40"]


@pytest.mark.parametrize(
    ("kw", "reason"), [({"idle_s": 0.3}, "idle"), ({"idle_s": 60.0, "max_s": 0.5}, "max")]
)
async def test_timers_close_the_session(work: Path, kw: dict[str, float], reason: str) -> None:
    m = _manager(work, **kw)
    s = m.open(80, 24)
    async for _frame in m.stream(s.id):
        pass
    assert s.done.is_set() and s.reason == reason and s.exit_code is not None
    with pytest.raises(ProcessLookupError):
        os.kill(s.pid, 0)


async def test_input_keeps_an_idle_session_open(work: Path) -> None:
    m = _manager(work, idle_s=0.6)
    s = m.open(80, 24)
    for _ in range(4):
        await asyncio.sleep(0.3)
        await m.write(s.id, b"\n")
    assert s.reason is None
    await m.shutdown()
    assert s.reason == "shutdown"


async def test_shell_exit_ends_the_session(pty: PtyManager) -> None:
    s = pty.open(80, 24)
    await pty.write(s.id, b"exit\n")
    await asyncio.wait_for(s.done.wait(), 10)
    assert s.reason == "exited"
    with pytest.raises(NotFound):
        await pty.write(s.id, b"x")


async def test_one_session_at_a_time(pty: PtyManager) -> None:
    s = pty.open(80, 24)
    with pytest.raises(Busy):
        pty.open(80, 24)
    await pty.close(s.id)
    again = pty.open(80, 24)
    assert again.id != s.id
    with pytest.raises(NotFound):
        pty.get(s.id)


# ── recording ──
async def test_recording_is_valid_asciicast_and_private(pty: PtyManager, work: Path) -> None:
    s = pty.open(120, 30)
    await pty.write(s.id, "echo $((6*7)) é\n".encode())
    await _read_until(pty, s, b"42")
    await pty.close(s.id)
    path = work / "recordings" / f"{s.id}.cast"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    header, events = _events(path)
    assert header["version"] == 2 and (header["width"], header["height"]) == (120, 30)
    assert isinstance(header["timestamp"], int) and header["env"]["TERM"] == TERM
    times = [e[0] for e in events]
    assert all(isinstance(t, float | int) and t >= 0 for t in times) and times == sorted(times)
    assert all(e[1] in ("o", "i", "r") and isinstance(e[2], str) for e in events)
    assert "".join(e[2] for e in events if e[1] == "i") == "echo $((6*7)) é\n"
    assert "42" in "".join(e[2] for e in events if e[1] == "o")


async def test_full_recording_ends_the_session(work: Path) -> None:
    m = _manager(work, recording_bytes=4096)
    s = m.open(80, 24)
    await m.write(s.id, b"i=0; while [ $i -lt 500 ]; do echo line $i; i=$((i+1)); done\n")
    await asyncio.wait_for(s.done.wait(), 10)
    assert s.reason == "recording_full"
    assert (work / "recordings" / f"{s.id}.cast").stat().st_size < 4096 + FRAME_BYTES * 2


async def test_symlinked_recordings_dir_is_refused(work: Path) -> None:
    (work / "elsewhere").mkdir()
    (work / "recordings").symlink_to(work / "elsewhere")
    with pytest.raises(RuntimeError):
        _manager(work).open(80, 24)


# ── output frames ──
async def test_big_output_comes_in_capped_frames_and_resumes(pty: PtyManager) -> None:
    s = pty.open(80, 24)
    await pty.write(s.id, b"head -c 100000 /dev/zero | tr '\\0' a; echo; echo DO''NE\n")
    frames: list[dict[str, Any]] = []

    async def follow() -> None:
        async for frame in pty.stream(s.id):
            frames.append(frame)
            if b"DONE\r\n" in base64.b64decode(frame["data"]):
                return

    await asyncio.wait_for(follow(), 20)
    sizes = [len(base64.b64decode(f["data"])) for f in frames]
    assert len(frames) > 1 and max(sizes) <= FRAME_BYTES
    assert sum(base64.b64decode(f["data"]).count(b"a") for f in frames) >= 100_000
    seqs = [f["seq"] for f in frames]
    assert seqs == sorted(seqs)
    resumed = await anext(pty.stream(s.id, from_seq=seqs[0] + 1))
    first = next(data for seq, data in s.chunks if seq == seqs[0] + 1)
    assert base64.b64decode(resumed["data"]).startswith(first)


# ── handlers and audit ──
async def test_handlers_validate_args(pty: PtyManager) -> None:
    h = pty.handlers()
    sid = (await h["pty.open"]({"cols": 80, "rows": 24}))["session_id"]
    bad: list[tuple[str, dict[str, Any]]] = [
        ("pty.open", {"cols": 0, "rows": 24}),
        ("pty.open", {"cols": 80, "rows": 24, "shell": "/bin/bash"}),
        ("pty.resize", {"session_id": sid, "cols": 501, "rows": 24}),
        ("pty.write", {"session_id": sid, "data": "not base64!"}),
        ("pty.write", {"session_id": sid, "data": base64.b64encode(b"x" * 16385).decode()}),
        ("pty.close", {"session_id": "../etc"}),
    ]
    for cmd, args in bad:
        with pytest.raises(InvalidArgs):
            await h[cmd](args)
    with pytest.raises(Busy):
        await h["pty.open"]({"cols": 80, "rows": 24})
    with pytest.raises(NotFound):
        await h["pty.write"]({"session_id": "0" * 32, "data": "eA=="})
    assert await h["pty.write"]({"session_id": sid, "data": "eA=="}) == {}
    assert await h["pty.resize"]({"session_id": sid, "cols": 90, "rows": 20}) == {
        "cols": 90,
        "rows": 20,
    }
    closed = await h["pty.close"]({"session_id": sid})
    assert closed["session_id"] == sid and closed["reason"] == "closed"
    assert closed["duration_ms"] >= 0


async def test_open_and_close_go_to_the_job_audit(work: Path) -> None:
    jobs = JobManager()
    m = _manager(work, audit=jobs.record)
    s = m.open(80, 24)
    await m.close(s.id)
    records = jobs.audit()["records"]
    assert [r["event"] for r in records] == ["pty.open", "pty.close"]
    assert all(r["session_id"] == s.id and r["peer_uid"] is None for r in records)
    assert records[1]["reason"] == "closed" and records[1]["duration_ms"] >= 0
    assert [r["seq"] for r in records] == [1, 2]


# ── launch and env ──
def _which(found: set[str]) -> Any:
    def which(name: str, path: str | None = None) -> str | None:
        return f"/usr/bin/{name}" if name in found else None

    return which


@pytest.mark.parametrize(
    ("account", "found", "argv"),
    [
        (
            "/usr/bin/zsh",
            {"tmux", "zsh", "bash"},
            ["/usr/bin/tmux", "new-session", "-A", "-s", TMUX_SESSION, "/usr/bin/zsh", "-l"],
        ),
        ("/bin/bash", {"zsh", "bash"}, ["/usr/bin/zsh", "-l"]),
        (
            "/bin/bash",
            {"tmux", "bash"},
            ["/usr/bin/tmux", "new-session", "-A", "-s", TMUX_SESSION, "/usr/bin/bash", "-l"],
        ),
    ],
)
def test_default_launch(account: str, found: set[str], argv: list[str]) -> None:
    launch = default_launch({"PATH": "/usr/bin"}, _which(found), account)
    assert launch.argv == argv and launch.shell == argv[-2]


def test_terminal_env_keeps_agent_settings_out() -> None:
    env = terminal_env(
        "/bin/zsh",
        {"PATH": "/usr/bin", "HOME": "/home/u", "CASINO_AGENT_TOKEN_FILE": "/x", "SECRET": "y"},
    )
    assert env["TERM"] == TERM and env["SHELL"] == "/bin/zsh" and env["PATH"] == "/usr/bin"
    assert not any(k.startswith("CASINO_AGENT") or k == "SECRET" for k in env)


# ── through the socket ──
@pytest.fixture
async def agent(work: Path) -> AsyncIterator[tuple[AgentServer, PtyManager]]:
    m = _manager(work)
    config = AgentConfig(
        socket_path=work / "a.sock",
        token=TOKEN,
        allow_uids=frozenset({os.getuid()}),
        allow_gids=frozenset(),
    )
    server = AgentServer(config, handlers={**DEFAULT_HANDLERS, **m.handlers()})
    await server.start()
    yield server, m
    await m.shutdown()
    await server.close()


async def _call(server: AgentServer, req_id: str, cmd: str, args: dict[str, Any]) -> Any:
    reader, writer = await asyncio.open_unix_connection(str(server.config.socket_path))
    try:
        msg = signed_request(TOKEN, req_id, cmd, int(time.time() * 1000), args)
        writer.write(json.dumps(msg).encode() + b"\n")
        await writer.drain()
        return json.loads(await asyncio.wait_for(reader.readline(), 10))
    finally:
        writer.close()


async def test_terminal_over_the_socket(agent: tuple[AgentServer, PtyManager]) -> None:
    server, _m = agent
    opened = await _call(server, "o1", "pty.open", {"cols": 80, "rows": 24})
    assert opened["ok"] is True
    sid = opened["data"]["session_id"]
    reader, writer = await asyncio.open_unix_connection(
        str(server.config.socket_path), limit=1 << 20
    )
    try:
        msg = signed_request(
            TOKEN, "s1", "pty.stream", int(time.time() * 1000), {"session_id": sid}
        )
        writer.write(json.dumps(msg).encode() + b"\n")
        await writer.drain()
        wrote = await _call(
            server,
            "w1",
            "pty.write",
            {"session_id": sid, "data": base64.b64encode(b"echo $((40+2))\n").decode()},
        )
        assert wrote == {"id": "w1", "ok": True, "data": {}}
        seen = b""
        while b"42" not in seen:
            item = json.loads(await asyncio.wait_for(reader.readline(), 10))
            Chunk.model_validate(item)
            seen += base64.b64decode(item["data"])
        closed = await _call(server, "c1", "pty.close", {"session_id": sid})
        assert closed["data"]["reason"] == "closed"
        while True:
            item = json.loads(await asyncio.wait_for(reader.readline(), 10))
            if "ok" in item:
                assert item["data"]["reason"] == "closed"
                break
    finally:
        writer.close()
    again = await _call(server, "c2", "pty.write", {"session_id": sid, "data": "eA=="})
    assert again["error"]["code"] == "not_found"


async def test_streaming_handler_uses_chunk_frames(pty: PtyManager) -> None:
    s = pty.open(80, 24)
    result = await pty.handlers()["pty.stream"]({"session_id": s.id})
    assert isinstance(result, Streaming) and result.frame is Chunk
