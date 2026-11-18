"""Jobs: start, stream (live and resumed), cancel, timeout, one mutating job per service, audit.

A fake program stands in for wolfctl, wolfbet.py and journalctl. It reads what to do
for its subcommand from plan.json: lines to print, stderr lines, a sleep, a child to
spawn, whether to ignore SIGTERM and the exit code.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import textwrap
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agent.config import AgentConfig
from agent.errors import Busy, NotFound
from agent.jobs import LINE_CAP, JobManager, clean_line
from agent.peercred import Peer, current_peer
from agent.protocol import Streaming, signed_request
from agent.server import DEFAULT_HANDLERS, AgentServer
from agent.services.wolfbet import WolfBetPaths, WolfBetService
from agent.validators import InvalidArgs

FAKE = textwrap.dedent("""
    import json, os, signal, subprocess, sys, time
    here = os.path.dirname(os.path.abspath(__file__))
    key = next((a for a in sys.argv[1:] if not a.startswith("/")), "")
    plan = json.load(open(os.path.join(here, "plan.json"))).get(key, {})
    if plan.get("ignore_term"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if plan.get("child"):
        child = subprocess.Popen(["sleep", "60"])
        open(os.path.join(here, "child.pid"), "w").write(str(child.pid))
    for line in plan.get("lines", []):
        print(line, flush=True)
    for line in plan.get("stderr", []):
        print(line, file=sys.stderr, flush=True)
    time.sleep(plan.get("sleep", 0))
    sys.exit(plan.get("exit", 0))
""")


@pytest.fixture
def bin_dir() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="caj-", dir="/tmp"))
    exe = path / "fake"
    exe.write_text(f"#!{sys.executable}\n" + FAKE)
    exe.chmod(0o755)
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _plan(bin_dir: Path, **plan: dict[str, Any]) -> None:
    (bin_dir / "plan.json").write_text(json.dumps(plan))


@dataclass
class Setup:
    jobs: JobManager
    service: WolfBetService
    bin_dir: Path


@pytest.fixture
def setup(bin_dir: Path) -> Setup:
    fake = bin_dir / "fake"
    paths = WolfBetPaths(
        python=fake,
        bot=bin_dir / "wolfbet.py",
        wolfctl=fake,
        repo=bin_dir,
        export_dir=bin_dir,
        journalctl=fake,
    )
    jobs = JobManager(cancel_grace_s=1.0)
    service = WolfBetService(paths, busy=lambda: jobs.mutating_running("wolfbet"))
    jobs.register("wolfbet", service)
    return Setup(jobs, service, bin_dir)


async def _all(frames: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]]:
    return [frame async for frame in frames]


async def _done(setup: Setup, job_id: str) -> dict[str, Any]:
    await asyncio.wait_for(setup.jobs.get(job_id).done.wait(), 10)
    return setup.jobs.get(job_id).summary()


# ── output ──
def test_clean_line_strips_escapes_and_caps_the_length() -> None:
    assert clean_line(b"\x1b[31mred\x1b[0m \x1b]0;title\x07done\r") == "red done"
    assert clean_line(b"tab\there\x00\x07") == "tab\there"
    assert clean_line("café".encode() + b"\xff") == "café�"
    long = clean_line(b"x" * (LINE_CAP + 100))
    assert long == "x" * LINE_CAP + " [truncated]"


async def test_stream_follows_a_job_to_the_end(setup: Setup) -> None:
    _plan(
        setup.bin_dir,
        update={
            "lines": ["pulling", "\x1b[32mok\x1b[0m", "y" * 20000, "copied"],
            "stderr": ["warn: dirty"],
        },
    )
    job = await setup.jobs.start("wolfbet.update", {})
    frames = await _all(setup.jobs.stream(job.id))
    stdout = [f["line"] for f in frames if f["stream"] == "stdout"]
    assert stdout == ["pulling", "ok", "y" * LINE_CAP + " [truncated]", "copied"]
    assert [f["line"] for f in frames if f["stream"] == "stderr"] == ["warn: dirty"]
    assert sorted(f["seq"] for f in frames) == list(range(5))
    summary = await _done(setup, job.id)
    assert summary["status"] == "succeeded" and summary["exit_code"] == 0
    assert summary["lines"] == 5 and summary["cmd"] == "wolfbet.update"


async def test_failed_exit_code(setup: Setup) -> None:
    _plan(setup.bin_dir, doctor={"lines": ["checking"], "exit": 3})
    job = await setup.jobs.start("wolfbet.doctor", {"fix": True})
    summary = await _done(setup, job.id)
    assert summary["status"] == "failed" and summary["exit_code"] == 3


async def test_stream_resumes_from_a_seq_and_keeps_the_newest_lines(setup: Setup) -> None:
    _plan(setup.bin_dir, update={"lines": [f"line {i}" for i in range(20)]})
    setup.jobs.buffer_lines = 5
    job = await setup.jobs.start("wolfbet.update", {})
    await _done(setup, job.id)
    assert [f["seq"] for f in await _all(setup.jobs.stream(job.id))] == [15, 16, 17, 18, 19]
    assert [f["line"] for f in await _all(setup.jobs.stream(job.id, 18))] == ["line 18", "line 19"]
    assert await _all(setup.jobs.stream(job.id, 20)) == []


async def test_stream_waits_for_new_lines(setup: Setup) -> None:
    _plan(setup.bin_dir, update={"lines": ["first"], "sleep": 0.5})
    job = await setup.jobs.start("wolfbet.update", {})
    frames = await asyncio.wait_for(_all(setup.jobs.stream(job.id)), 10)
    assert [f["line"] for f in frames] == ["first"]
    assert setup.jobs.get(job.id).status == "succeeded"


# ── one mutating job per service ──
async def test_second_mutating_job_or_command_is_busy(setup: Setup) -> None:
    _plan(setup.bin_dir, update={"sleep": 30}, **{"--backtest": {"lines": ["ran"]}})
    update = await setup.jobs.start("wolfbet.update", {})
    with pytest.raises(Busy):
        await setup.jobs.start("wolfbet.restart", {})
    with pytest.raises(Busy):
        await setup.service.run_command("start", {})
    with pytest.raises(Busy):
        await setup.service.config_set({"pairs": {"maxloss": "0.01"}})
    backtest = await setup.jobs.start("wolfbet.backtest", {"simulate": 100})
    assert (await _done(setup, backtest.id))["status"] == "succeeded"
    await setup.jobs.cancel(update.id)
    restart = await setup.jobs.start("wolfbet.restart", {})
    assert (await _done(setup, restart.id))["status"] == "succeeded"


@pytest.mark.parametrize(
    "cmd,args",
    [
        ("wolfbet.status", {}),
        ("wolfbet.nope", {}),
        ("other.update", {}),
        ("wolfbet.update", {"branch": "-x"}),
        ("wolfbet.backtest", {"simulate": 0}),
    ],
)
async def test_start_refuses_short_unknown_or_bad_commands(
    setup: Setup, cmd: str, args: dict[str, Any]
) -> None:
    with pytest.raises(InvalidArgs):
        await setup.jobs.start(cmd, args)
    assert setup.jobs.jobs == {}


# ── cancel and timeout ──
async def test_cancel_kills_the_process_group(setup: Setup) -> None:
    _plan(setup.bin_dir, update={"child": True, "lines": ["started"], "sleep": 60})
    job = await setup.jobs.start("wolfbet.update", {})
    for _ in range(200):
        if (setup.bin_dir / "child.pid").exists():
            break
        await asyncio.sleep(0.02)
    child = int((setup.bin_dir / "child.pid").read_text())
    summary = (await setup.jobs.cancel(job.id)).summary()
    assert summary["status"] == "cancelled"
    await asyncio.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


async def test_cancel_escalates_to_sigkill(setup: Setup) -> None:
    _plan(setup.bin_dir, doctor={"ignore_term": True, "lines": ["stubborn"], "sleep": 60})
    job = await setup.jobs.start("wolfbet.doctor", {})
    for _ in range(500):  # its first line comes after it starts ignoring SIGTERM
        if job.next_seq:
            break
        await asyncio.sleep(0.01)
    summary = (await asyncio.wait_for(setup.jobs.cancel(job.id), 10)).summary()
    assert summary["status"] == "cancelled" and summary["exit_code"] == -9


async def test_timeout_kills_and_marks_the_job(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from agent.services import wolfbet

    monkeypatch.setitem(
        wolfbet.COMMANDS, "update", replace(wolfbet.COMMANDS["update"], timeout_s=0.5)
    )
    _plan(setup.bin_dir, update={"sleep": 30})
    job = await setup.jobs.start("wolfbet.update", {})
    summary = await _done(setup, job.id)
    assert summary["status"] == "timeout" and summary["exit_code"] == -9


async def test_update_clears_the_config_schema_cache(setup: Setup) -> None:
    setup.service._schema = {"keys": []}
    _plan(setup.bin_dir, update={})
    job = await setup.jobs.start("wolfbet.update", {})
    await _done(setup, job.id)
    assert setup.service._schema is None


# ── get, not found, audit ──
async def test_unknown_job_is_not_found(setup: Setup) -> None:
    with pytest.raises(NotFound):
        setup.jobs.get("0" * 32)
    handlers = setup.jobs.handlers()
    with pytest.raises(NotFound):
        await handlers["job.cancel"]({"job_id": "f" * 32})
    with pytest.raises(InvalidArgs):
        await handlers["job.get"]({"job_id": "../x"})


async def test_audit_records_start_and_finish_with_the_caller(setup: Setup) -> None:
    _plan(setup.bin_dir, update={"exit": 1}, doctor={})
    token = current_peer.set(Peer(uid=10001, gid=997))
    try:
        first = await setup.jobs.start("wolfbet.update", {"branch": "main"})
        await _done(setup, first.id)
        second = await setup.jobs.start("wolfbet.doctor", {})
        await _done(setup, second.id)
    finally:
        current_peer.reset(token)
    page = setup.jobs.audit(after=0, limit=3)
    assert [(r["event"], r["job_id"]) for r in page["records"]] == [
        ("start", first.id),
        ("finish", first.id),
        ("start", second.id),
    ]
    start, finish = page["records"][0], page["records"][1]
    assert start["args"] == {"branch": "main"} and start["peer_uid"] == 10001
    assert finish["status"] == "failed" and finish["exit_code"] == 1 and finish["duration_ms"] >= 0
    rest = setup.jobs.audit(after=page["next"])
    assert [r["event"] for r in rest["records"]] == ["finish"] and rest["next"] == 4
    assert setup.jobs.audit(after=4) == {"records": [], "next": 4}


# ── through the socket ──
TOKEN = b"Kq9vX2mLp7RtZ4wYb8NcFj3HdGs6Ae1U-test-token"


@pytest.fixture
async def agent(setup: Setup) -> AsyncIterator[AgentServer]:
    config = AgentConfig(
        socket_path=setup.bin_dir / "a.sock",
        token=TOKEN,
        allow_uids=frozenset({os.getuid()}),
        allow_gids=frozenset(),
    )
    handlers = {**DEFAULT_HANDLERS, **setup.service.handlers(), **setup.jobs.handlers()}
    server = AgentServer(config, handlers=handlers)
    await server.start()
    yield server
    await server.close()


async def _send(writer: asyncio.StreamWriter, cmd: str, args: dict[str, Any], req_id: str) -> None:
    import time

    msg = signed_request(TOKEN, req_id, cmd, int(time.time() * 1000), args)
    writer.write(json.dumps(msg).encode() + b"\n")
    await writer.drain()


async def test_job_stream_over_the_socket(setup: Setup, agent: AgentServer) -> None:
    _plan(setup.bin_dir, update={"lines": ["one", "two"], "stderr": ["err"]})
    reader, writer = await asyncio.open_unix_connection(str(agent.config.socket_path))
    await _send(writer, "job.start", {"cmd": "wolfbet.update"}, "s1")
    started = json.loads(await reader.readline())
    job_id = started["data"]["job_id"]
    await _send(writer, "job.stream", {"job_id": job_id}, "s2")
    lines = []
    while True:
        msg = json.loads(await asyncio.wait_for(reader.readline(), 10))
        if "ok" in msg:
            final = msg
            break
        assert set(msg) == {"id", "seq", "stream", "line"} and msg["id"] == "s2"
        lines.append((msg["stream"], msg["line"]))
    assert sorted(lines) == [("stderr", "err"), ("stdout", "one"), ("stdout", "two")]
    assert final["id"] == "s2" and final["data"]["status"] == "succeeded"
    await _send(writer, "job.start", {"cmd": "wolfbet.update", "args": {"branch": ";id"}}, "s3")
    assert json.loads(await reader.readline())["error"] == {"code": "bad_request"}
    writer.close()


async def test_busy_and_not_found_over_the_socket(setup: Setup, agent: AgentServer) -> None:
    _plan(setup.bin_dir, update={"sleep": 30})
    reader, writer = await asyncio.open_unix_connection(str(agent.config.socket_path))
    await _send(writer, "job.start", {"cmd": "wolfbet.update"}, "b1")
    job_id = json.loads(await reader.readline())["data"]["job_id"]
    await _send(writer, "job.start", {"cmd": "wolfbet.restart"}, "b2")
    assert json.loads(await reader.readline())["error"] == {"code": "busy"}
    await _send(writer, "wolfbet.start", {}, "b3")
    assert json.loads(await reader.readline())["error"] == {"code": "busy"}
    await _send(writer, "job.get", {"job_id": "a" * 32}, "b4")
    assert json.loads(await reader.readline())["error"] == {"code": "not_found"}
    await _send(writer, "job.cancel", {"job_id": job_id}, "b5")
    assert (
        json.loads(await asyncio.wait_for(reader.readline(), 10))["data"]["status"] == "cancelled"
    )
    writer.close()


async def test_client_leaving_mid_stream_does_not_hurt_the_agent(
    setup: Setup, agent: AgentServer
) -> None:
    _plan(setup.bin_dir, logs_follow={}, **{"--user": {"lines": ["a"] * 3, "sleep": 30}})
    reader, writer = await asyncio.open_unix_connection(str(agent.config.socket_path))
    await _send(writer, "job.start", {"cmd": "wolfbet.logs_follow"}, "c1")
    job_id = json.loads(await reader.readline())["data"]["job_id"]
    await _send(writer, "job.stream", {"job_id": job_id}, "c2")
    assert json.loads(await asyncio.wait_for(reader.readline(), 10))["line"] == "a"
    writer.close()
    await asyncio.sleep(0.1)
    reader2, writer2 = await asyncio.open_unix_connection(str(agent.config.socket_path))
    await _send(writer2, "job.cancel", {"job_id": job_id}, "c3")
    assert (
        json.loads(await asyncio.wait_for(reader2.readline(), 10))["data"]["status"] == "cancelled"
    )
    writer2.close()


async def test_job_stream_handler_returns_streaming(setup: Setup) -> None:
    _plan(setup.bin_dir, update={"lines": ["x"]})
    job = await setup.jobs.start("wolfbet.update", {})
    result = await setup.jobs.handlers()["job.stream"]({"job_id": job.id})
    assert isinstance(result, Streaming)
    assert [f["line"] for f in await _all(result.frames)] == ["x"]
    assert (await result.final())["status"] == "succeeded"
