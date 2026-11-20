"""Publish host, unit, container and publisher numbers on the service's system topic.

One task samples once a second for every viewer, never per socket: the agent's `metrics` op
(host, the bot's units, the Redis and Timescale containers) plus each lane's event-publisher
counters from its latest state event. When the agent is unreachable the sample still goes out,
with `agent: "unavailable"` and the publisher counters only.
"""

import asyncio
import time
from typing import Any, Protocol

import structlog

from app.agent_client import AgentError
from app.live.aggregator import Aggregator
from app.live.resync import Agent

EVERY_S = 1.0

log = structlog.get_logger("app.live.system")


class SystemSink(Protocol):
    def publish_system(self, body: dict[str, Any]) -> None: ...


class SystemPublisher:
    def __init__(self, hub: SystemSink, aggregator: Aggregator, agent: Agent | None) -> None:
        self.hub = hub
        self.aggregator = aggregator
        self.agent = agent
        self._failing = False
        self.agent_ok: bool | None = None
        self.agent_ms: float | None = None

    async def publish_once(self) -> None:
        body: dict[str, Any] = {"ts_ms": int(time.time() * 1000), "agent": "unavailable"}
        if self.agent is not None:
            started = time.monotonic()
            try:
                data = await self.agent.call("metrics")
            except AgentError as error:
                self.agent_ok = False
                if not self._failing:
                    log.warning("system metrics unavailable", error=error.code)
                    self._failing = True
            else:
                self.agent_ok = True
                self.agent_ms = (time.monotonic() - started) * 1000
                if self._failing:
                    log.info("system metrics back")
                    self._failing = False
                body["agent"] = "ok"
                for key in ("host", "units", "containers"):
                    body[key] = data.get(key)
        body["publisher"] = {
            number: lane.publisher
            for number, lane in sorted(self.aggregator.lanes.items())
            if lane.publisher is not None
        }
        self.hub.publish_system(body)

    async def run(self, stop: asyncio.Event) -> None:
        """Sample once a second until stop is set; a failed round is logged and retried."""
        while not stop.is_set():
            try:
                await self.publish_once()
            except Exception:
                log.exception("system round failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=EVERY_S)
            except TimeoutError:
                pass
