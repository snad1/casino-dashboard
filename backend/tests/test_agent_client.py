"""The agent client signs requests the way the agent verifies them and never leaks the token."""

from pathlib import Path
from typing import Any

import pytest

from app.agent_client import AgentClient, AgentError, StreamEnd
from tests.fake_agent import TOKEN, FakeAgent, fail, ok


async def test_a_signed_call_returns_the_reply_data(fake_agent: FakeAgent) -> None:
    fake_agent.reply = lambda request: [ok(request, {"json": {"running": True}})]
    client = AgentClient(fake_agent.path, TOKEN + "\n")
    assert await client.call("wolfbet.status", {"lane": 2}) == {"json": {"running": True}}
    [request] = fake_agent.calls
    assert request["mac_ok"]
    assert (request["proto"], request["cmd"], request["args"]) == (1, "wolfbet.status", {"lane": 2})
    assert len(request["id"]) == 32


async def test_an_error_reply_carries_only_the_agent_code(fake_agent: FakeAgent) -> None:
    fake_agent.reply = lambda request: [fail(request, "unauthorized")]
    client = AgentClient(fake_agent.path, TOKEN)
    with pytest.raises(AgentError) as caught:
        await client.call("wolfbet.status")
    assert caught.value.code == "unauthorized"
    assert TOKEN not in str(caught.value)
    assert TOKEN not in repr(client)


async def test_a_silent_agent_times_out(fake_agent: FakeAgent) -> None:
    fake_agent.hang = True
    client = AgentClient(fake_agent.path, TOKEN, timeout=0.1)
    with pytest.raises(AgentError) as caught:
        await client.call("wolfbet.status")
    assert caught.value.code == "timeout"


async def test_a_missing_socket_is_unreachable() -> None:
    client = AgentClient(Path("/nonexistent/agent.sock"), TOKEN)
    with pytest.raises(AgentError) as caught:
        await client.call("wolfbet.status")
    assert caught.value.code == "unreachable"


async def test_reply_data_that_is_not_an_object_reads_as_empty(fake_agent: FakeAgent) -> None:
    fake_agent.reply = lambda _: [{"ok": True, "data": "not an object"}]
    client = AgentClient(fake_agent.path, TOKEN)
    assert await client.call("wolfbet.status") == {}


async def _drain(client: AgentClient) -> list[Any]:
    return [item async for item in client.stream("job.stream", {"job_id": "x", "from_seq": 0})]


async def test_a_stream_yields_its_frames_then_the_final_reply(fake_agent: FakeAgent) -> None:
    client = AgentClient(fake_agent.path, TOKEN)
    *frames, end = await _drain(client)
    assert [frame["line"] for frame in frames] == ["step 0", "step 1"]
    assert isinstance(end, StreamEnd)
    assert end.data["status"] == "succeeded"
    assert fake_agent.calls[0]["mac_ok"]


async def test_a_stream_error_reply_raises_the_agent_code(fake_agent: FakeAgent) -> None:
    fake_agent.reply = lambda request: [fail(request, "not_found")]
    with pytest.raises(AgentError) as caught:
        await _drain(AgentClient(fake_agent.path, TOKEN))
    assert caught.value.code == "not_found"


async def test_a_stream_cut_off_before_its_reply_is_unreachable(fake_agent: FakeAgent) -> None:
    fake_agent.reply = lambda request: [
        {"id": request["id"], "seq": 0, "stream": "stdout", "line": "a"}
    ]
    with pytest.raises(AgentError) as caught:
        await _drain(AgentClient(fake_agent.path, TOKEN))
    assert caught.value.code == "unreachable"
