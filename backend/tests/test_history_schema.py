"""The history migration builds the bets hypertable, its rollups and their policies."""

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.settings import Settings
from tests.conftest import alembic_config

ROLLUPS = ("bets_1m", "bets_1h", "bets_1d")
T0 = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


Step = Callable[[Connection], None]
Run = Callable[[Step], None]


@pytest.fixture
def on_db(migrated: Settings) -> Run:
    """Run a step on an autocommit connection; refreshing a rollup refuses a transaction."""

    def run(step: Step) -> None:
        async def go() -> None:
            engine = create_async_engine(
                migrated.database_url, poolclass=NullPool, isolation_level="AUTOCOMMIT"
            )
            try:
                async with engine.connect() as connection:
                    await connection.run_sync(step)
            finally:
                await engine.dispose()

        asyncio.run(go())

    return run


def _epoch(db: Connection, service: str) -> int:
    epoch_id: int = db.execute(
        text(
            "INSERT INTO db_epochs (service, source_path, inode, first_started_at)"
            " VALUES (:service, '/botdata/wolfbet.db', 42, :t) RETURNING epoch_id"
        ),
        {"service": service, "t": T0},
    ).scalar_one()
    return epoch_id


def _bet(
    db: Connection,
    service: str,
    epoch_id: int,
    src_id: int,
    ts: datetime,
    *,
    lane: int = 1,
    state: str = "win",
    amount: float = 1.0,
    profit: float = 1.0,
    balance: float = 100.0,
) -> int:
    return db.execute(
        text(
            "INSERT INTO bets (service, epoch_id, src_id, ts, session_src_id, lane, amount,"
            " state, profit, balance_after)"
            " VALUES (:service, :epoch, :src, :ts, 1, :lane, :amount, :state, :profit, :balance)"
            " ON CONFLICT DO NOTHING"
        ),
        {
            "service": service,
            "epoch": epoch_id,
            "src": src_id,
            "ts": ts,
            "lane": lane,
            "amount": amount,
            "state": state,
            "profit": profit,
            "balance": balance,
        },
    ).rowcount


def _cleanup(db: Connection, service: str) -> None:
    db.execute(text("DELETE FROM bets WHERE service = :s"), {"s": service})
    db.execute(text("DELETE FROM sessions WHERE service = :s"), {"s": service})
    db.execute(text("DELETE FROM db_epochs WHERE service = :s"), {"s": service})


def test_bets_is_compressed_hypertable_without_retention(on_db: Run) -> None:
    def check(db: Connection) -> None:
        compressed = db.execute(
            text(
                "SELECT compression_enabled FROM timescaledb_information.hypertables"
                " WHERE hypertable_name = 'bets'"
            )
        ).scalar_one()
        assert compressed is True
        jobs = db.execute(
            text(
                "SELECT proc_name, config->>'compress_after' FROM timescaledb_information.jobs"
                " WHERE hypertable_name = 'bets'"
            )
        ).all()
        assert [tuple(j) for j in jobs] == [("policy_compression", "7 days")]

    on_db(check)


def test_rollups_are_continuous_aggregates_with_refresh_policies(on_db: Run) -> None:
    def check(db: Connection) -> None:
        refreshed = set(
            db.execute(
                text(
                    "SELECT ca.view_name FROM timescaledb_information.continuous_aggregates ca"
                    " JOIN timescaledb_information.jobs j ON j.hypertable_name = ca.view_name"
                    " WHERE j.proc_name = 'policy_refresh_continuous_aggregate'"
                )
            ).scalars()
        )
        assert set(ROLLUPS) <= refreshed

    on_db(check)


def test_duplicate_bet_is_rejected(on_db: Run) -> None:
    service = f"svc-{uuid.uuid4().hex[:8]}"

    def check(db: Connection) -> None:
        epoch_id = _epoch(db, service)
        assert _bet(db, service, epoch_id, 1, T0) == 1
        assert _bet(db, service, epoch_id, 1, T0) == 0
        with pytest.raises(IntegrityError):
            db.execute(
                text(
                    "INSERT INTO bets (service, epoch_id, src_id, ts, lane)"
                    " VALUES (:service, :epoch, 1, :ts, 1)"
                ),
                {"service": service, "epoch": epoch_id, "ts": T0},
            )

    try:
        on_db(check)
    finally:
        on_db(lambda db: _cleanup(db, service))


def _refresh(db: Connection) -> None:
    for name in ROLLUPS:
        db.execute(text(f"CALL refresh_continuous_aggregate('{name}', NULL, NULL)"))


def test_rollups_sum_per_lane_after_refresh(on_db: Run) -> None:
    service = f"svc-{uuid.uuid4().hex[:8]}"
    minute = timedelta(minutes=1)
    bets = [
        (1, T0, 1, "win", 1.0, 0.5, 100.5),
        (2, T0 + timedelta(seconds=20), 1, "loss", 2.0, -2.0, 98.5),
        (3, T0 + timedelta(seconds=40), 2, "win", 1.0, 1.0, 50.0),
        (4, T0 + minute, 1, "win", 1.0, 0.5, 99.0),
        (5, T0 + minute + timedelta(seconds=30), 1, "loss", 4.0, -4.0, 95.0),
        (6, T0 + minute + timedelta(seconds=10), 2, "loss", 3.0, -3.0, 47.0),
    ]
    query = (
        "SELECT bucket, lane, bets, wins, wagered, profit, last_balance FROM {view}"
        " WHERE service = :s ORDER BY bucket, lane"
    )

    def check(db: Connection) -> None:
        epoch_id = _epoch(db, service)
        for src, ts, lane, state, amount, profit, balance in bets:
            _bet(
                db,
                service,
                epoch_id,
                src,
                ts,
                lane=lane,
                state=state,
                amount=amount,
                profit=profit,
                balance=balance,
            )
        _refresh(db)
        by_minute = db.execute(text(query.format(view="bets_1m")), {"s": service}).all()
        assert [tuple(r) for r in by_minute] == [
            (T0, 1, 2, 1, 3.0, -1.5, 98.5),
            (T0, 2, 1, 1, 1.0, 1.0, 50.0),
            (T0 + minute, 1, 2, 1, 5.0, -3.5, 95.0),
            (T0 + minute, 2, 1, 0, 3.0, -3.0, 47.0),
        ]
        expected = [(1, 4, 2, 8.0, -5.0, 95.0), (2, 2, 1, 4.0, -2.0, 47.0)]
        for view, start in (("bets_1h", T0), ("bets_1d", T0.replace(hour=0))):
            rows = db.execute(text(query.format(view=view)), {"s": service}).all()
            assert [tuple(r) for r in rows] == [(start, *e) for e in expected]

    def clean(db: Connection) -> None:
        _cleanup(db, service)
        _refresh(db)

    try:
        on_db(check)
    finally:
        on_db(clean)


HISTORY = {"bets", "sessions", "db_epochs", *ROLLUPS}


def test_downgrade_removes_history_and_keeps_audit(migrated: Settings, on_db: Run) -> None:
    seen: list[set[str]] = []

    def relations(db: Connection) -> None:
        inspector = inspect(db)
        seen.append(set(inspector.get_table_names()) | set(inspector.get_view_names()))

    config = alembic_config(migrated)
    command.downgrade(config, "0004")
    try:
        on_db(relations)
    finally:
        command.upgrade(config, "head")
    on_db(relations)
    downgraded, upgraded = seen
    assert "audit_log" in downgraded
    assert not HISTORY & downgraded
    assert HISTORY <= upgraded
