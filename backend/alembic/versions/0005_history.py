"""History store: database epochs, sessions, the bets hypertable and its rollups.

`bets` is chunked by day and compressed once a chunk is 7 days old. There is no retention
policy: history is kept forever, because the bot purges old bets from its own SQLite and
this copy is then the only record. Compressed bets take a few bytes each, so revisit only
if disk use says so.

`bets_1m`, `bets_1h` and `bets_1d` roll bets up per service and lane. The hourly and daily
views are built on the one below them. They are created empty and filled by their refresh
policies; rows older than a policy's start offset (archive backfill) need an explicit
`refresh_continuous_aggregate` call. Recent, not yet materialized buckets are read live.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BETS_1M = """
    SELECT time_bucket(INTERVAL '1 minute', ts) AS bucket, service, lane,
           count(*) AS bets,
           count(*) FILTER (WHERE state = 'win') AS wins,
           sum(amount) AS wagered,
           sum(profit) AS profit,
           last(balance_after, ts) AS last_balance
    FROM bets
    GROUP BY 1, service, lane
"""
BETS_1H = """
    SELECT time_bucket(INTERVAL '1 hour', bucket) AS bucket, service, lane,
           sum(bets) AS bets, sum(wins) AS wins, sum(wagered) AS wagered,
           sum(profit) AS profit, last(last_balance, bucket) AS last_balance
    FROM bets_1m
    GROUP BY 1, service, lane
"""
BETS_1D = """
    SELECT time_bucket(INTERVAL '1 day', bucket) AS bucket, service, lane,
           sum(bets) AS bets, sum(wins) AS wins, sum(wagered) AS wagered,
           sum(profit) AS profit, last(last_balance, bucket) AS last_balance
    FROM bets_1h
    GROUP BY 1, service, lane
"""
ROLLUPS = (
    # name, query, refresh start offset, end offset, schedule
    ("bets_1m", BETS_1M, "2 hours", "1 minute", "1 minute"),
    ("bets_1h", BETS_1H, "3 days", "1 hour", "30 minutes"),
    ("bets_1d", BETS_1D, "30 days", "1 day", "6 hours"),
)


def upgrade() -> None:
    op.create_table(
        "db_epochs",
        sa.Column("epoch_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("inode", sa.BigInteger(), nullable=False),
        sa.Column("first_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("epoch_id", name=op.f("pk_db_epochs")),
        sa.UniqueConstraint(
            "service", "inode", "first_started_at", name=op.f("uq_db_epochs_service")
        ),
    )
    op.create_table(
        "sessions",
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("epoch_id", sa.BigInteger(), nullable=False),
        sa.Column("src_id", sa.BigInteger(), nullable=False),
        sa.Column("lane", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("currency", sa.Text(), nullable=True),
        sa.Column("game", sa.Text(), nullable=True),
        sa.Column("strategy", sa.Text(), nullable=True),
        sa.Column("base_bet", sa.Double(), nullable=True),
        sa.Column("multiplier", sa.Double(), nullable=True),
        sa.Column("total_bets", sa.BigInteger(), nullable=True),
        sa.Column("wins", sa.BigInteger(), nullable=True),
        sa.Column("losses", sa.BigInteger(), nullable=True),
        sa.Column("profit", sa.Double(), nullable=True),
        sa.Column("wagered", sa.Double(), nullable=True),
        sa.Column("start_balance", sa.Double(), nullable=True),
        sa.Column("end_balance", sa.Double(), nullable=True),
        sa.Column("max_win_streak", sa.BigInteger(), nullable=True),
        sa.Column("max_loss_streak", sa.BigInteger(), nullable=True),
        sa.Column("highest_balance", sa.Double(), nullable=True),
        sa.Column("lowest_balance", sa.Double(), nullable=True),
        sa.Column("highest_win", sa.Double(), nullable=True),
        sa.Column("biggest_loss", sa.Double(), nullable=True),
        sa.Column("bets_per_minute", sa.Double(), nullable=True),
        sa.Column("bets_per_second", sa.Double(), nullable=True),
        sa.Column("peak_bps", sa.Double(), nullable=True),
        sa.Column("low_bps", sa.Double(), nullable=True),
        sa.Column("peak_bpm", sa.Double(), nullable=True),
        sa.Column("low_bpm", sa.Double(), nullable=True),
        sa.ForeignKeyConstraint(
            ["epoch_id"],
            ["db_epochs.epoch_id"],
            name=op.f("fk_sessions_epoch_id_db_epochs"),
        ),
        sa.PrimaryKeyConstraint("service", "epoch_id", "src_id", name=op.f("pk_sessions")),
    )
    op.create_table(
        "bets",
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("epoch_id", sa.BigInteger(), nullable=False),
        sa.Column("src_id", sa.BigInteger(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_src_id", sa.BigInteger(), nullable=True),
        sa.Column("lane", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Double(), nullable=True),
        sa.Column("multiplier", sa.Double(), nullable=True),
        sa.Column("rule", sa.Text(), nullable=True),
        sa.Column("bet_value", sa.Double(), nullable=True),
        sa.Column("result_value", sa.Double(), nullable=True),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("profit", sa.Double(), nullable=True),
        sa.Column("balance_after", sa.Double(), nullable=True),
        sa.PrimaryKeyConstraint("service", "epoch_id", "src_id", "ts", name=op.f("pk_bets")),
    )
    op.create_index("ix_bets_session", "bets", ["service", "epoch_id", "session_src_id", "ts"])
    op.execute(
        "SELECT create_hypertable('bets', by_range('ts', INTERVAL '1 day'),"
        " create_default_indexes => false)"
    )
    op.execute(
        "ALTER TABLE bets SET (timescaledb.compress,"
        " timescaledb.compress_segmentby = 'service, lane',"
        " timescaledb.compress_orderby = 'ts, epoch_id, src_id')"
    )
    op.execute("SELECT add_compression_policy('bets', INTERVAL '7 days')")

    for name, query, start, end, every in ROLLUPS:
        op.execute(
            f"CREATE MATERIALIZED VIEW {name}"
            " WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS"
            f" {query} WITH NO DATA"
        )
        op.execute(
            f"SELECT add_continuous_aggregate_policy('{name}',"
            f" start_offset => INTERVAL '{start}', end_offset => INTERVAL '{end}',"
            f" schedule_interval => INTERVAL '{every}')"
        )


def downgrade() -> None:
    for name, *_ in reversed(ROLLUPS):
        op.execute(f"DROP MATERIALIZED VIEW {name}")
    op.drop_table("bets")
    op.drop_table("sessions")
    op.drop_table("db_epochs")
