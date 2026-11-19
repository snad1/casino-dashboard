"""The system topic gets one sample a second: agent metrics plus each lane's publisher counters."""

from typing import Any

import structlog

from app.agent_client import AgentError
from app.live.aggregator import Aggregator
from app.live.system import SystemPublisher
from tests.test_aggregator import Clock, state

METRICS = {
    "host": {"cpu_pct": 12.5, "load": [0.5, 0.4, 0.3]},
    "units": [{"unit": "wolfbet", "cpu_pct": 30.0, "rss_bytes": 1000}],
    "containers": [{"service": "redis", "cpu_pct": 1.0, "memory_bytes": 2000}],
}


class FakeAgent:
    def __init__(self) -> None:
        self.failing = False
        self.calls: list[str] = []

    async def call(self, cmd: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append(cmd)
        if self.failing:
            raise AgentError("unreachable")
        return dict(METRICS)


class FakeHub:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def publish_system(self, body: dict[str, Any]) -> None:
        self.sent.append(body)


def _aggregator() -> Aggregator:
    aggregator = Aggregator("wolfbet", clock=Clock())
    events = {"enabled": True, "connected": True, "sent": 10, "dropped": 1, "overflow": 0}
    aggregator.apply(state(lane=2, session_id=1, events=events))
    aggregator.apply(state(lane=1, session_id=1))
    return aggregator


async def test_a_round_sends_agent_metrics_and_publisher_counters() -> None:
    hub, agent = FakeHub(), FakeAgent()
    await SystemPublisher(hub, _aggregator(), agent).publish_once()
    [body] = hub.sent
    assert agent.calls == ["metrics"]
    assert body["agent"] == "ok"
    assert (body["host"], body["units"], body["containers"]) == (
        METRICS["host"],
        METRICS["units"],
        METRICS["containers"],
    )
    assert body["publisher"] == {
        2: {"enabled": True, "connected": True, "sent": 10, "queued": 0, "dropped": 1}
    }
    assert isinstance(body["ts_ms"], int)


async def test_an_unreachable_agent_still_sends_publisher_counters_and_logs_once() -> None:
    hub, agent = FakeHub(), FakeAgent()
    agent.failing = True
    publisher = SystemPublisher(hub, _aggregator(), agent)
    with structlog.testing.capture_logs() as logs:
        await publisher.publish_once()
        await publisher.publish_once()
    assert [b["agent"] for b in hub.sent] == ["unavailable", "unavailable"]
    assert all("host" not in b and 2 in b["publisher"] for b in hub.sent)
    assert [e["event"] for e in logs].count("system metrics unavailable") == 1


async def test_without_an_agent_only_publisher_counters_go_out() -> None:
    hub = FakeHub()
    await SystemPublisher(hub, _aggregator(), None).publish_once()
    assert hub.sent[0]["agent"] == "unavailable"
    assert 2 in hub.sent[0]["publisher"]
