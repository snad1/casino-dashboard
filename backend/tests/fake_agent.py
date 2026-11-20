"""A stand-in for the host agent on a real unix socket, on its own thread and event loop.

It serves sync tests (the app under `TestClient` runs on another loop) and async ones
alike. `reply` maps each request to the lines it answers with, frames first, either as a
list or as an async iterator that writes each line as it comes; `hang` makes it read the
request and never answer.
"""

import asyncio
import contextlib
import hashlib
import hmac
import json
import secrets
import tempfile
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

TOKEN = secrets.token_urlsafe(48)
JOB_ID = "ab" * 16
Lines = list[dict[str, Any]] | AsyncIterator[dict[str, Any]]
Reply = Callable[[dict[str, Any]], Lines]


def agent_mac(fields: dict[str, Any]) -> str:
    """The agent's own rule: canonical JSON of every field but `mac`."""
    body = {k: v for k, v in fields.items() if k != "mac"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hmac.new(TOKEN.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def ok(request: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    return {"id": request["id"], "ok": True, "data": data}


def fail(request: dict[str, Any], code: str) -> dict[str, Any]:
    return {"id": request["id"], "ok": False, "error": {"code": code}}


def job_summary(status: str = "running", exit_code: int | None = None) -> dict[str, Any]:
    return {
        "job_id": JOB_ID,
        "cmd": "wolfbet.update",
        "status": status,
        "exit_code": exit_code,
        "started_ms": 1,
        "duration_ms": 5,
        "lines": 2,
    }


def default_reply(request: dict[str, Any]) -> list[dict[str, Any]]:
    """A healthy agent: commands exit 0, jobs start and stream two lines then succeed."""
    cmd = request["cmd"]
    if cmd == "job.start":
        return [ok(request, {"job_id": JOB_ID})]
    if cmd == "job.get":
        return [ok(request, job_summary())]
    if cmd == "job.cancel":
        return [ok(request, job_summary("cancelled", -15))]
    if cmd == "job.stream":
        frames = [
            {"id": request["id"], "seq": n, "stream": "stdout", "line": f"step {n}"}
            for n in range(2)
        ]
        return [*frames, ok(request, job_summary("succeeded", 0))]
    if cmd == "wolfbet.config_set":
        return [ok(request, {"dry_run": {"exit_code": 0}, "set": {"exit_code": 0}})]
    return [ok(request, {"exit_code": 0, "stderr": "", "stdout": "done"})]


class FakeAgent:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls: list[dict[str, Any]] = []
        self.reply: Reply = default_reply
        self.hang = False
        self._loop = asyncio.new_event_loop()
        self._server: asyncio.Server | None = None
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)

    def cmds(self) -> list[str]:
        return [call["cmd"] for call in self.calls]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = json.loads(await reader.readline())
        request["mac_ok"] = hmac.compare_digest(agent_mac(request), request["mac"])
        self.calls.append(request)
        if self.hang:
            await asyncio.sleep(5)
        else:
            lines = self.reply(request)
            with contextlib.suppress(ConnectionError):  # the api may end a stream early
                if isinstance(lines, list):
                    for line in lines:
                        writer.write(json.dumps(line).encode() + b"\n")
                else:
                    async for line in lines:
                        writer.write(json.dumps(line).encode() + b"\n")
                        await writer.drain()
                await writer.drain()
        writer.close()

    def start(self) -> None:
        self._thread.start()

        async def serve() -> asyncio.Server:
            return await asyncio.start_unix_server(self._handle, path=str(self.path))

        self._server = asyncio.run_coroutine_threadsafe(serve(), self._loop).result(5)

    def stop(self) -> None:
        async def close() -> None:
            if self._server is not None:
                self._server.close()

        asyncio.run_coroutine_threadsafe(close(), self._loop).result(5)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)


@pytest.fixture
def fake_agent() -> Iterator[FakeAgent]:
    """A running fake agent; the socket path is short, as unix sockets need on macOS."""
    with tempfile.TemporaryDirectory(prefix="ag") as directory:
        agent = FakeAgent(Path(directory) / "a.sock")
        agent.start()
        try:
            yield agent
        finally:
            agent.stop()
