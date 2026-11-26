"""One service's live pipeline: its stream, its model, its sockets and what runs beside them.

Each served service gets its own pipeline in its own tasks, so one bot's outage (a refused
stream, a silent agent) never stalls another's live view.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import registry
from app.agent_client import AgentClient
from app.alerts import digest
from app.alerts.engine import Engine
from app.live.aggregator import Aggregator, TickTimer, run_ticker
from app.live.consumer import ServiceConsumer
from app.live.resync import Resync
from app.live.system import SystemPublisher
from app.ws.hub import Hub

log = structlog.get_logger("app.live")


@dataclass
class LivePipeline:
    svc: str
    aggregator: Aggregator
    hub: Hub
    consumer: ServiceConsumer
    resync: Resync
    system: SystemPublisher
    alerts: Engine

    async def start_alerts(self) -> None:
        try:
            await self.alerts.start()
        except Exception:
            log.exception(
                "alert rules could not be loaded; alerts are off until restart", svc=self.svc
            )

    def tasks(
        self,
        stop: asyncio.Event,
        hz: int,
        ticks: TickTimer,
        sessionmaker: async_sessionmaker[AsyncSession],
        host_tz: str,
    ) -> list[asyncio.Task[Any]]:
        return [
            asyncio.create_task(self.consumer.run(stop)),
            asyncio.create_task(run_ticker(self.aggregator, self.hub.publish, stop, hz, ticks)),
            asyncio.create_task(self.alerts.run(stop, self.aggregator)),
            asyncio.create_task(digest.run(stop, sessionmaker, self.svc, host_tz)),
            asyncio.create_task(self.hub.run_heartbeat(stop)),
            asyncio.create_task(self.resync.run(stop)),
            asyncio.create_task(self.system.run(stop)),
        ]


def build(
    svc: str,
    redis: Redis,
    sessionmaker: async_sessionmaker[AsyncSession],
    agent: AgentClient | None,
    host_tz: str,
) -> LivePipeline:
    aggregator = Aggregator(svc)
    hub = Hub(svc, aggregator)
    consumer = ServiceConsumer(redis, registry.KNOWN[svc].adapter(), aggregator.accept)
    resync = Resync(
        aggregator, sessionmaker, agent, reconnects=lambda: consumer.stats["reconnects"]
    )
    return LivePipeline(
        svc=svc,
        aggregator=aggregator,
        hub=hub,
        consumer=consumer,
        resync=resync,
        system=SystemPublisher(hub, aggregator, agent),
        alerts=Engine(svc, sessionmaker, host_tz),
    )
