"""The engine against Timescale: a firing lands with its deliveries and state, once."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import asyncpg  # type: ignore[import-untyped]  # ships no type hints
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.engine import Engine, after_reconcile

T0 = datetime(2026, 11, 20, 12, 0, tzinfo=UTC).timestamp()


class Model:
    """Stands in for the aggregator: the lanes as of the latest tick."""

    def __init__(self) -> None:
        self.fields: dict[int, dict[str, Any]] = {}
        self.lanes: dict[int, Any] = {}

    def set(self, lane: int, dropped: int | None = None, **fields: Any) -> None:
        self.fields[lane] = {"status": "running", "session_id": 1, **fields}
        publisher = None if dropped is None else {"dropped": dropped}
        self.lanes[lane] = SimpleNamespace(publisher=publisher)

    def snapshot(self) -> tuple[dict[int, dict[str, Any]], list[Any]]:
        return dict(self.fields), []


@pytest.fixture
async def rules(pg: asyncpg.Connection, service: str) -> AsyncIterator[Any]:
    async def add(
        kind: str, params: dict[str, Any], cooldown_s: int = 0, lane: int | None = None
    ) -> int:
        rule_id: int = await pg.fetchval(
            "INSERT INTO alert_rules (service, lane, kind, name, params, cooldown_s, enabled)"
            " VALUES ($1, $2, $3, 'Rule', $4::jsonb, $5, true) RETURNING id",
            service,
            lane,
            kind,
            json.dumps(params),
            cooldown_s,
        )
        return rule_id

    yield add
    await pg.execute("DELETE FROM alert_events WHERE service = $1", service)
    await pg.execute("DELETE FROM alert_rules WHERE service = $1", service)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> float:
        return self.now


async def events(pg: asyncpg.Connection, service: str) -> list[asyncpg.Record]:
    return list(
        await pg.fetch(
            "SELECT e.id, e.lane, e.value, e.message, array_agg(o.channel ORDER BY o.channel) AS"
            " channels, array_agg(o.status) AS statuses FROM alert_events e"
            " JOIN alert_outbox o ON o.event_id = e.id WHERE e.service = $1"
            " GROUP BY e.id ORDER BY e.id",
            service,
        )
    )


async def tick(
    engine: Engine, model: Model, clock: Clock, steps: list[tuple[float, dict[str, Any]]]
) -> None:
    for seconds, fields in steps:
        clock.now = T0 + seconds
        model.set(1, **fields)
        engine.on_tick(model)  # type: ignore[arg-type]
        await engine.flush()


async def test_milestones_write_one_event_with_three_deliveries_each(
    sessions: async_sessionmaker[AsyncSession], pg: asyncpg.Connection, service: str, rules: Any
) -> None:
    await rules("profit", {"step": 0.001}, lane=1)
    clock, model = Clock(), Model()
    engine = Engine(service, sessions, "UTC", clock)
    await engine.start()
    await tick(
        engine,
        model,
        clock,
        [(i, {"profit": p}) for i, p in enumerate([0.0009, 0.0011, 0.0019, 0.0021])],
    )

    rows = await events(pg, service)
    assert [round(r["value"], 6) for r in rows] == [0.001, 0.002]
    assert all(r["channels"] == ["email", "push", "telegram"] for r in rows)
    assert all(set(r["statuses"]) == {"pending"} for r in rows)
    assert rows[0]["message"] == "Rule: profit passed +0.00100000 on lane 1"


async def test_a_restart_while_the_bot_is_still_down_fires_nothing_new(
    sessions: async_sessionmaker[AsyncSession], pg: asyncpg.Connection, service: str, rules: Any
) -> None:
    await rules("down", {"statuses": ["down"], "for_s": 60}, cooldown_s=600)
    clock, model = Clock(), Model()
    engine = Engine(service, sessions, "UTC", clock)
    await engine.start()
    await tick(engine, model, clock, [(0, {"status": "down"}), (61, {"status": "down"})])
    assert len(await events(pg, service)) == 1

    restarted = Engine(service, sessions, "UTC", clock)
    await restarted.start()
    await tick(restarted, model, clock, [(62, {"status": "down"}), (200, {"status": "down"})])
    assert len(await events(pg, service)) == 1

    await tick(
        restarted,
        model,
        clock,
        [(300, {"status": "running"}), (400, {"status": "down"}), (470, {"status": "down"})],
    )
    assert len(await events(pg, service)) == 1, "the cooldown still holds"
    await tick(
        restarted,
        model,
        clock,
        [(700, {"status": "running"}), (710, {"status": "down"}), (780, {"status": "down"})],
    )
    assert len(await events(pg, service)) == 2


async def test_day_profit_is_seeded_from_todays_rollup(
    sessions: async_sessionmaker[AsyncSession], pg: asyncpg.Connection, service: str, rules: Any
) -> None:
    epoch = await pg.fetchval(
        "INSERT INTO db_epochs (service, source_path, inode, first_started_at)"
        " VALUES ($1, '/botdata/x.db', 1, now()) RETURNING epoch_id",
        service,
    )
    today = datetime.fromtimestamp(T0, UTC)
    for src, (when, profit) in enumerate(
        [(today - timedelta(days=1), 5.0), (today - timedelta(hours=2), 0.4)], 1
    ):
        await pg.execute(
            "INSERT INTO bets (service, epoch_id, src_id, ts, lane, profit)"
            " VALUES ($1, $2, $3, $4, 1, $5)",
            service,
            epoch,
            src,
            when,
            profit,
        )
    await rules("profit", {"period": "day", "at": 0.5})
    clock, model = Clock(), Model()
    engine = Engine(service, sessions, "UTC", clock)
    await engine.start()
    assert engine.day["total"] == pytest.approx(0.4)
    await tick(engine, model, clock, [(0, {"profit": 0.0}), (1, {"profit": 0.2})])
    rows = await events(pg, service)
    assert len(rows) == 1 and rows[0]["lane"] is None
    assert rows[0]["value"] == pytest.approx(0.6)


async def test_ingest_rules_fire_on_a_reconcile_run_once_until_it_clears(
    sessions: async_sessionmaker[AsyncSession], pg: asyncpg.Connection, service: str, rules: Any
) -> None:
    await rules("reconcile_mismatch", {})
    await rules("ingest_lag", {"seconds": 300})
    run = {"mismatched": 2, "lag_seconds": 10.0, "lag_alert": False}
    assert await after_reconcile(sessions, service, run, now=T0) == 1
    assert await after_reconcile(sessions, service, run, now=T0 + 900) == 0
    clear = {"mismatched": 0, "lag_seconds": 400.0, "lag_alert": False}
    assert await after_reconcile(sessions, service, clear, now=T0 + 1800) == 1
    assert await after_reconcile(sessions, service, run, now=T0 + 2700) == 1
    messages = [r["message"] for r in await events(pg, service)]
    assert messages == [
        "Rule: 2 sessions differ from the bot's database",
        "Rule: ingest 400 s behind",
        "Rule: 2 sessions differ from the bot's database",
    ]


async def test_disabled_and_broken_rules_are_skipped(
    sessions: async_sessionmaker[AsyncSession], pg: asyncpg.Connection, service: str, rules: Any
) -> None:
    broken = await rules("drawdown", {"pct": 500})
    off = await rules("down", {"for_s": 1})
    await pg.execute("UPDATE alert_rules SET enabled = false WHERE id = $1", off)
    engine = Engine(service, sessions, "UTC", Clock())
    await engine.start()
    assert engine.rules == []
    assert broken


async def test_fresh_installs_get_the_default_rules_switched_off(pg: asyncpg.Connection) -> None:
    rows = await pg.fetch(
        "SELECT kind, enabled FROM alert_rules WHERE service = 'wolfbet' ORDER BY id"
    )
    assert [r["kind"] for r in rows] == ["profit", "loss", "drawdown", "down", "stop_reason"]
    assert not any(r["enabled"] for r in rows)
