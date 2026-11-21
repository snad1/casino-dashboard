"""History tables: a copy of each bot's SQLite `sessions` and `bets`, keyed by database epoch.

SQLite ids restart when the bot's database is archived and recreated, so every copied row
carries the `epoch_id` of the database file it came from. `src_id` is the SQLite row id.
`bets` is a Timescale hypertable on `ts`; its primary key must include `ts`.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class DbEpoch(Base):
    """One SQLite database file lifetime, told apart by inode and first session start.

    `tg_uid` is the Telegram user whose file it is, for a bot that keeps one file per user.
    """

    __tablename__ = "db_epochs"
    __table_args__ = (
        UniqueConstraint("service", "inode", "first_started_at"),
        Index("ix_db_epochs_service_tg_uid", "service", "tg_uid"),
    )

    epoch_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    service: Mapped[str] = mapped_column(Text)
    source_path: Mapped[str] = mapped_column(Text)
    inode: Mapped[int] = mapped_column(BigInteger)
    first_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tg_uid: Mapped[int | None] = mapped_column(BigInteger)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class HistorySession(Base):
    """A bot session with its running totals, as last read from SQLite."""

    __tablename__ = "sessions"

    service: Mapped[str] = mapped_column(Text, primary_key=True)
    epoch_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("db_epochs.epoch_id"), primary_key=True
    )
    src_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    lane: Mapped[int] = mapped_column(BigInteger)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    currency: Mapped[str | None] = mapped_column(Text)
    game: Mapped[str | None] = mapped_column(Text)
    strategy: Mapped[str | None] = mapped_column(Text)
    base_bet: Mapped[float | None] = mapped_column(Double)
    multiplier: Mapped[float | None] = mapped_column(Double)
    total_bets: Mapped[int | None] = mapped_column(BigInteger)
    wins: Mapped[int | None] = mapped_column(BigInteger)
    losses: Mapped[int | None] = mapped_column(BigInteger)
    profit: Mapped[float | None] = mapped_column(Double)
    wagered: Mapped[float | None] = mapped_column(Double)
    start_balance: Mapped[float | None] = mapped_column(Double)
    end_balance: Mapped[float | None] = mapped_column(Double)
    max_win_streak: Mapped[int | None] = mapped_column(BigInteger)
    max_loss_streak: Mapped[int | None] = mapped_column(BigInteger)
    highest_balance: Mapped[float | None] = mapped_column(Double)
    lowest_balance: Mapped[float | None] = mapped_column(Double)
    highest_win: Mapped[float | None] = mapped_column(Double)
    biggest_loss: Mapped[float | None] = mapped_column(Double)
    bets_per_minute: Mapped[float | None] = mapped_column(Double)
    bets_per_second: Mapped[float | None] = mapped_column(Double)
    peak_bps: Mapped[float | None] = mapped_column(Double)
    low_bps: Mapped[float | None] = mapped_column(Double)
    peak_bpm: Mapped[float | None] = mapped_column(Double)
    low_bpm: Mapped[float | None] = mapped_column(Double)


class Bet(Base):
    """One bet. `lane` is copied from its session at ingest, since SQLite bets have no lane
    and continuous aggregates cannot join."""

    __tablename__ = "bets"
    __table_args__ = (Index("ix_bets_session", "service", "epoch_id", "session_src_id", "ts"),)

    service: Mapped[str] = mapped_column(Text, primary_key=True)
    epoch_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    src_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    session_src_id: Mapped[int | None] = mapped_column(BigInteger)
    lane: Mapped[int] = mapped_column(BigInteger)
    amount: Mapped[float | None] = mapped_column(Double)
    multiplier: Mapped[float | None] = mapped_column(Double)
    rule: Mapped[str | None] = mapped_column(Text)
    bet_value: Mapped[float | None] = mapped_column(Double)
    result_value: Mapped[float | None] = mapped_column(Double)
    state: Mapped[str | None] = mapped_column(Text)
    profit: Mapped[float | None] = mapped_column(Double)
    balance_after: Mapped[float | None] = mapped_column(Double)


class ReconcileRun(Base):
    """One comparison of SQLite and Timescale bets for bet ids in (lo_id, hi_id].

    `persisting` counts sessions that were already flagged by the previous run, so one
    mismatch older than a cycle is enough to alert on. `lag_alert` means ingest lags by more
    than half the bot's purge window, so unread bets may be deleted before they are copied.
    """

    __tablename__ = "reconcile_runs"
    __table_args__ = (Index("ix_reconcile_runs_service_started", "service", "started_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    service: Mapped[str] = mapped_column(Text)
    epoch_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("db_epochs.epoch_id"))
    kind: Mapped[str] = mapped_column(Text)
    lo_id: Mapped[int] = mapped_column(BigInteger)
    hi_id: Mapped[int] = mapped_column(BigInteger)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sessions_checked: Mapped[int] = mapped_column(Integer)
    mismatched: Mapped[int] = mapped_column(Integer)
    purged_upstream: Mapped[int] = mapped_column(Integer)
    persisting: Mapped[int] = mapped_column(Integer)
    lag_rows: Mapped[int] = mapped_column(BigInteger)
    lag_seconds: Mapped[float] = mapped_column(Double)
    lag_alert: Mapped[bool] = mapped_column(Boolean)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)
