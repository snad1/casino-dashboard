"""The agent client signs requests the way the agent verifies them and never leaks the token."""

import asyncio
import hashlib
import hmac
import json
import secrets
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from app.agent_client import AgentClient, AgentError

TOKEN = secrets.token_urlsafe(48)
Reply = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]


def _agent_mac(fields: dict[str, Any]) -> str:
    """The agent's own rule: canonical JSON of every field but `mac`."""
    body = {k: v for k, v in fields.items() if k != "mac"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hmac.new(TOKEN.encode(), canonical.encode(), hashlib.sha256).hexdigest()


@pytest.fixture
async def fake_agent() -> AsyncIterator[Callable[[Reply], Awaitable[Path]]]:
    """Start an agent stand-in; socket paths are short, as unix sockets need on macOS."""
    servers: list[asyncio.Server] = []
    directory = tempfile.TemporaryDirectory(prefix="ag")

    async def start(reply: Reply) -> Path:
        path = Path(directory.name) / f"{len(servers)}.sock"

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            request = json.loads(await reader.readline())
            answer = await reply(request)
            if answer is not None:
                writer.write(json.dumps(answer).encode() + b"\n")
                await writer.drain()
            writer.close()

        servers.append(await asyncio.start_unix_server(handle, path=str(path)))
        return path

    yield start
    for server in servers:
        server.close()
        await server.wait_closed()
    directory.cleanup()


async def test_a_signed_call_returns_the_reply_data(fake_agent: Any) -> None:
    seen: list[dict[str, Any]] = []

    async def reply(request: dict[str, Any]) -> dict[str, Any]:
        seen.append(request)
        ok = hmac.compare_digest(_agent_mac(request), request["mac"])
        return {"id": request["id"], "ok": ok, "data": {"json": {"running": True}}}

    client = AgentClient(await fake_agent(reply), TOKEN + "\n")
    assert await client.call("wolfbet.status", {"lane": 2}) == {"json": {"running": True}}
    [request] = seen
    assert (request["proto"], request["cmd"], request["args"]) == (1, "wolfbet.status", {"lane": 2})
    assert len(request["id"]) == 32


async def test_an_error_reply_carries_only_the_agent_code(fake_agent: Any) -> None:
    async def reply(request: dict[str, Any]) -> dict[str, Any]:
        return {"id": request["id"], "ok": False, "error": {"code": "unauthorized"}}

    client = AgentClient(await fake_agent(reply), TOKEN)
    with pytest.raises(AgentError) as caught:
        await client.call("wolfbet.status")
    assert caught.value.code == "unauthorized"
    assert TOKEN not in str(caught.value)
    assert TOKEN not in repr(client)


async def test_a_silent_agent_times_out(fake_agent: Any) -> None:
    async def reply(_: dict[str, Any]) -> None:
        await asyncio.sleep(1)

    client = AgentClient(await fake_agent(reply), TOKEN, timeout=0.1)
    with pytest.raises(AgentError) as caught:
        await client.call("wolfbet.status")
    assert caught.value.code == "timeout"


async def test_a_missing_socket_is_unreachable() -> None:
    client = AgentClient(Path("/nonexistent/agent.sock"), TOKEN)
    with pytest.raises(AgentError) as caught:
        await client.call("wolfbet.status")
    assert caught.value.code == "unreachable"


async def test_reply_data_that_is_not_an_object_reads_as_empty(fake_agent: Any) -> None:
    async def reply(_: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "data": "not an object"}

    client = AgentClient(await fake_agent(reply), TOKEN)
    assert await client.call("wolfbet.status") == {}
