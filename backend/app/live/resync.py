"""Bring the live model back in line after an api start, a stream gap or a Redis reconnect.

The event stream is lossy by design, so a lane is re-seeded from the durable session totals in
Postgres and then from the daemon's own state, read through the host agent. A lane that has
gone quiet is checked the same way: the agent says whether its daemon is still alive (`down`
when not), and the bot's `events_enabled` setting tells a bot that simply sends no events
apart from a dead one.

Each lane is resynced at most once per `RESYNC_EVERY_S`, and the bot config at most once per
`CONFIG_EVERY_S`, so a flapping stream never floods the agent. An unreachable agent leaves the
daemon state unknown (`stale`), never guessed as `down`.
"""

import asyncio
from collections.abc import Callable
from typing import Any, Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_client import AgentError
from app.api.history import current_sessions
from app.history.models import HistorySession
from app.live.aggregator import STALE_AFTER_S, Aggregator

RESYNC_EVERY_S = 10.0
CONFIG_EVERY_S = 30.0
CHECK_EVERY_S = 1.0

log = structlog.get_logger("app.live.resync")


class Agent(Protocol):
    async def call(self, cmd: str, args: dict[str, Any] | None = None) -> dict[str, Any]: ...


class Resync:
    """Decides which lanes need a resync and runs it; `pass_once` is one decision round."""

    def __init__(
        self,
        aggregator: Aggregator,
        sessionmaker: async_sessionmaker[AsyncSession],
        agent: Agent | None,
        reconnects: Callable[[], int] = lambda: 0,
    ) -> None:
        self.aggregator = aggregator
        self.sessionmaker = sessionmaker
        self.agent = agent
        self.reconnects = reconnects
        self._reconnects_seen = reconnects()
        self._everything = True
        self._gaps_seen: dict[int, int] = {}
        self._last: dict[int, float] = {}
        self._config_at: float | None = None
        self._agent_failing = False

    def _wanted(self, now: float) -> set[int]:
        if self.reconnects() != self._reconnects_seen:
            self._reconnects_seen = self.reconnects()
            self._everything = True
        wanted = set()
        for number, lane in self.aggregator.lanes.items():
            if lane.gaps != self._gaps_seen.get(number, 0):
                wanted.add(number)
            if lane.last_seen is None or now - lane.last_seen > STALE_AFTER_S:
                wanted.add(number)
        return wanted

    async def pass_once(self) -> None:
        now = self.aggregator.clock()
        wanted = self._wanted(now)
        if not (wanted or self._everything):
            return
        async with self.sessionmaker() as db:
            rows = await current_sessions(db, self.aggregator.svc)
        stored = {row.lane: _session(row) for row in rows}
        if self._everything:
            wanted |= stored.keys() | self.aggregator.lanes.keys()
            self._everything = False
        due = {n for n in wanted if now - self._last.get(n, -RESYNC_EVERY_S) >= RESYNC_EVERY_S}
        if not due:
            return
        await self._read_config(now)
        for number in sorted(due):
            self._last[number] = now
            await self._resync_lane(number, stored.get(number))

    async def _read_config(self, now: float) -> None:
        if self.agent is None:
            return
        if self._config_at is not None and now - self._config_at < CONFIG_EVERY_S:
            return
        self._config_at = now
        doc = _json(await self._ask(self.agent, "wolfbet.config_show", {}))
        config = doc.get("config")
        enabled = config.get("events_enabled") if isinstance(config, dict) else None
        if isinstance(enabled, bool):
            self.aggregator.events_enabled = enabled

    async def _resync_lane(self, number: int, session: dict[str, Any] | None) -> None:
        lane = self.aggregator.lanes.get(number)
        was_running = lane.agent_running if lane is not None else None
        state = running = None
        if self.agent is not None:
            doc = _json(await self._ask(self.agent, "wolfbet.status", {"lane": number}))
            if isinstance(doc.get("running"), bool):
                running = doc["running"]
                state = doc.get("state") if isinstance(doc.get("state"), dict) else None
        self.aggregator.seed(number, session, state, running)
        lane = self.aggregator.lanes[number]
        self._gaps_seen[number] = lane.gaps
        if running is False and was_running is not False:
            log.warning("daemon down", svc=self.aggregator.svc, lane=number)

    async def _ask(self, agent: Agent, cmd: str, args: dict[str, Any]) -> dict[str, Any] | None:
        try:
            data = await agent.call(cmd, args)
        except AgentError as error:
            if not self._agent_failing:
                log.warning("agent unavailable", svc=self.aggregator.svc, error=error.code)
                self._agent_failing = True
            return None
        if self._agent_failing:
            log.info("agent back", svc=self.aggregator.svc)
            self._agent_failing = False
        return data

    async def run(self, stop: asyncio.Event) -> None:
        """Check once a second until stop is set; a failed round is logged and retried."""
        while not stop.is_set():
            try:
                await self.pass_once()
            except Exception:
                log.exception("resync round failed", svc=self.aggregator.svc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=CHECK_EVERY_S)
            except TimeoutError:
                pass


def _session(row: HistorySession) -> dict[str, Any]:
    return {
        "id": row.src_id,
        "total_bets": row.total_bets,
        "wins": row.wins,
        "losses": row.losses,
        "wagered": row.wagered,
        "profit": row.profit,
        "max_win_streak": row.max_win_streak,
        "max_loss_streak": row.max_loss_streak,
        "end_balance": row.end_balance,
        "currency": row.currency,
        "strategy": row.strategy,
    }


def _json(data: dict[str, Any] | None) -> dict[str, Any]:
    """The bot's JSON document from an agent reply, or empty when there is none."""
    doc = (data or {}).get("json")
    return doc if isinstance(doc, dict) else {}
