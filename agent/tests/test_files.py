"""Export files: one 0700 directory per job, read back only by id, never via a host path.

A fake wolfbet.py writes the export into the directory given by --export and prints
the same JSON as `wolfbet.py --export --json` (path, rows, bytes, sha256).
"""

import asyncio
import base64
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import textwrap
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from agent.config import AgentConfig
from agent.errors import NotFound
from agent.files import FileStore
from agent.jobs import JobManager
from agent.protocol import Chunk, Streaming, signed_request
from agent.server import DEFAULT_HANDLERS, AgentServer
from agent.services.wolfbet import WolfBetPaths, WolfBetService
from agent.validators import InvalidArgs

FAKE_BOT = textwrap.dedent("""
    import hashlib, json, os, sys, time
    argv = sys.argv[1:]
    here = os.path.dirname(os.path.abspath(__file__))
    mode = open(os.path.join(here, "mode")).read().strip() if os.path.exists(
        os.path.join(here, "mode")) else "ok"
    target = argv[argv.index("--export") + 1]
    sid = argv[argv.index("--export-session-id") + 1] if "--export-session-id" in argv else "1"
    path = os.path.join(target, f"wolfbet-session-{sid}.csv") if target.endswith("/") else target
    body = os.urandom(200_000) if mode != "empty" else b""
    if mode == "fail":
        print("Session not found", file=sys.stderr)
        sys.exit(1)
    if mode == "sleep":
        time.sleep(60)
    with open(path, "wb") as f:
        f.write(body)
    if mode == "two":
        open(os.path.join(os.path.dirname(path), "extra.txt"), "w").write("x")
    if mode == "link":
        os.remove(path)
        os.symlink("/etc/hosts", path)
    print(json.dumps({"v": 1, "session_id": int(sid), "path": path, "format": "csv", "rows": 3,
                      "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}))
""")


@pytest.fixture
def work() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="caf-", dir="/tmp"))
    bot = path / "fakebot"
    bot.write_text(f"#!{sys.executable}\n" + FAKE_BOT)
    bot.chmod(0o755)
    yield path
    shutil.rmtree(path, ignore_errors=True)


class Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


def _stack(
    work: Path, clock: Clock | None = None, cap: int = 512 * 1024 * 1024
) -> tuple[FileStore, JobManager, WolfBetService]:
    files = FileStore(work / "exports", max_total_bytes=cap, clock=clock or time.time)
    paths = WolfBetPaths(
        python=work / "fakebot",
        bot=work / "wolfbet.py",
        wolfctl=work / "fakebot",
        repo=work,
        export_dir=work / "exports",
        journalctl=work / "fakebot",
    )
    jobs = JobManager(files=files, cancel_grace_s=1.0)
    service = WolfBetService(paths, busy=lambda: jobs.mutating_running("wolfbet"), files=files)
    jobs.register("wolfbet", service)
    return files, jobs, service


async def _export(jobs: JobManager, **args: Any) -> dict[str, Any]:
    job = await jobs.start("wolfbet.export", args)
    await asyncio.wait_for(job.done.wait(), 20)
    return job.summary()


async def _read_all(stream: Streaming) -> tuple[bytes, dict[str, Any], list[dict[str, Any]]]:
    frames = [frame async for frame in stream.frames]
    body = b"".join(base64.b64decode(f["data"]) for f in frames)
    return body, await stream.final(), frames


def _printed(jobs: JobManager, job_id: str) -> dict[str, Any]:
    line = next(text for _, stream, text in jobs.get(job_id).lines if stream == "stdout")
    result: dict[str, Any] = json.loads(line)
    return result


# ── the happy path ──
async def test_export_job_hands_over_a_file_by_id(work: Path) -> None:
    files, jobs, _ = _stack(work)
    summary = await _export(jobs, session_id=42)
    assert summary["status"] == "succeeded"
    file_id = summary["file_id"]
    directory = work / "exports" / file_id
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((work / "exports").stat().st_mode) == 0o700
    printed = _printed(jobs, summary["job_id"])
    assert printed["path"] == "<export>/wolfbet-session-42.csv"
    assert str(work) not in json.dumps([line for _, _, line in jobs.get(summary["job_id"]).lines])
    body, final, frames = await _read_all(files.read(file_id))
    on_disk = (directory / "wolfbet-session-42.csv").read_bytes()
    assert body == on_disk and len(frames) == 4
    assert all(len(base64.b64decode(f["data"])) <= 64 * 1024 for f in frames)
    assert [f["seq"] for f in frames] == [0, 1, 2, 3]
    assert final == {
        "file_id": file_id,
        "name": "wolfbet-session-42.csv",
        "bytes": len(on_disk),
        "sha256": hashlib.sha256(on_disk).hexdigest(),
    }
    assert final["sha256"] == printed["sha256"]


async def test_json_export_keeps_its_name(work: Path) -> None:
    files, jobs, _ = _stack(work)
    summary = await _export(jobs, session_id=7, format="json")
    assert files.files[summary["file_id"]].name == "wolfbet-session-7.json"


# ── what never gets registered ──
@pytest.mark.parametrize("mode", ["fail", "two", "link"])
async def test_failed_or_odd_exports_leave_nothing(work: Path, mode: str) -> None:
    (work / "mode").write_text(mode)
    files, jobs, _ = _stack(work)
    summary = await _export(jobs)
    assert "file_id" not in summary
    assert files.files == {} and list((work / "exports").iterdir()) == []


async def test_cancelled_export_leaves_nothing(work: Path) -> None:
    (work / "mode").write_text("sleep")
    _files, jobs, _ = _stack(work)
    job = await jobs.start("wolfbet.export", {})
    await asyncio.sleep(0.3)
    await jobs.cancel(job.id)
    assert "file_id" not in job.summary() and list((work / "exports").iterdir()) == []


async def test_oversize_export_is_dropped_and_old_ones_evicted(work: Path) -> None:
    files, jobs, _ = _stack(work, cap=100_000)
    assert "file_id" not in await _export(jobs)
    assert list((work / "exports").iterdir()) == []
    files.max_total_bytes = 450_000
    first = (await _export(jobs))["file_id"]
    second = (await _export(jobs))["file_id"]
    third = (await _export(jobs))["file_id"]
    assert first not in files.files and {second, third} <= set(files.files)
    assert not (work / "exports" / first).exists()


# ── reading back ──
async def test_unknown_or_malformed_ids(work: Path) -> None:
    files, _, _ = _stack(work)
    with pytest.raises(NotFound):
        files.read("0" * 32)
    handler = files.handlers()["file.read"]
    for bad in ("../../etc/passwd", "0" * 31, "Z" * 32, "/" + "0" * 31):
        with pytest.raises(InvalidArgs):
            await handler({"file_id": bad})


async def test_file_swapped_for_a_symlink_is_refused(work: Path) -> None:
    files, jobs, _ = _stack(work)
    file_id = (await _export(jobs))["file_id"]
    path = work / "exports" / file_id / files.files[file_id].name
    path.unlink()
    path.symlink_to("/etc/hosts")
    with pytest.raises(NotFound):
        files.read(file_id)


async def test_file_replaced_by_another_size_is_refused(work: Path) -> None:
    files, jobs, _ = _stack(work)
    file_id = (await _export(jobs))["file_id"]
    path = work / "exports" / file_id / files.files[file_id].name
    path.write_bytes(b"short")
    with pytest.raises(NotFound):
        files.read(file_id)


async def test_directory_swapped_for_a_symlink_is_refused(work: Path) -> None:
    files, jobs, _ = _stack(work)
    file_id = (await _export(jobs))["file_id"]
    name = files.files[file_id].name
    directory = work / "exports" / file_id
    outside = work / "outside"
    outside.mkdir()
    (outside / name).write_bytes(b"x" * files.files[file_id].size)
    shutil.rmtree(directory)
    directory.symlink_to(outside)
    with pytest.raises(NotFound):
        files.read(file_id)


# ── expiry and sweeping ──
async def test_files_expire_after_the_ttl(work: Path) -> None:
    clock = Clock()
    files, jobs, _ = _stack(work, clock=clock)
    file_id = (await _export(jobs))["file_id"]
    clock.now += 3599
    files.read(file_id)
    clock.now += 2
    with pytest.raises(NotFound):
        files.read(file_id)
    assert files.sweep() == 1
    assert not (work / "exports" / file_id).exists()


def test_sweep_removes_leftovers_but_not_running_jobs(work: Path) -> None:
    files = FileStore(work / "exports")
    running_id, running_dir = files.allocate()
    (work / "exports" / "deadbeef").mkdir()
    (work / "exports" / "stray.txt").write_text("x")
    assert files.sweep() == 2
    assert sorted(p.name for p in (work / "exports").iterdir()) == [running_id]
    assert running_dir.is_dir()


async def test_sweep_forever_runs_until_cancelled(work: Path) -> None:
    files = FileStore(work / "exports")
    (work / "exports").mkdir(mode=0o700)
    (work / "exports" / "old").mkdir()
    task = asyncio.create_task(files.sweep_forever(interval_s=0.05))
    await asyncio.sleep(0.2)
    task.cancel()
    assert not (work / "exports" / "old").exists()


def test_export_root_that_is_a_symlink_is_refused(work: Path) -> None:
    real = work / "real"
    real.mkdir()
    (work / "exports").symlink_to(real)
    with pytest.raises(RuntimeError, match="not a directory"):
        FileStore(work / "exports").allocate()


# ── through the socket ──
TOKEN = b"Kq9vX2mLp7RtZ4wYb8NcFj3HdGs6Ae1U-test-token"


@pytest.fixture
async def agent(work: Path) -> AsyncIterator[tuple[AgentServer, FileStore, JobManager]]:
    files, jobs, service = _stack(work)
    config = AgentConfig(
        socket_path=work / "a.sock",
        token=TOKEN,
        allow_uids=frozenset({os.getuid()}),
        allow_gids=frozenset(),
    )
    handlers = {**DEFAULT_HANDLERS, **service.handlers(), **jobs.handlers(), **files.handlers()}
    server = AgentServer(config, handlers=handlers)
    await server.start()
    yield server, files, jobs
    await server.close()


async def test_file_read_over_the_socket(
    agent: tuple[AgentServer, FileStore, JobManager], work: Path
) -> None:
    server, _files, jobs = agent
    file_id = (await _export(jobs, session_id=3))["file_id"]
    reader, writer = await asyncio.open_unix_connection(
        str(server.config.socket_path), limit=1 << 20
    )
    try:
        msg = signed_request(
            TOKEN, "r1", "file.read", int(time.time() * 1000), {"file_id": file_id}
        )
        writer.write(json.dumps(msg).encode() + b"\n")
        await writer.drain()
        chunks = []
        while True:
            item = json.loads(await asyncio.wait_for(reader.readline(), 10))
            if "ok" in item:
                final = item
                break
            Chunk.model_validate(item)
            chunks.append(base64.b64decode(item["data"]))
        body = b"".join(chunks)
        assert hashlib.sha256(body).hexdigest() == final["data"]["sha256"]
        assert final["data"]["name"] == "wolfbet-session-3.csv" and str(work) not in json.dumps(
            final
        )
    finally:
        writer.close()
