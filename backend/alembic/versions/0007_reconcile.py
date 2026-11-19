"""Reconcile runs: each comparison of SQLite and Timescale bets, for alerts and audit.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reconcile_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("epoch_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("lo_id", sa.BigInteger(), nullable=False),
        sa.Column("hi_id", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sessions_checked", sa.Integer(), nullable=False),
        sa.Column("mismatched", sa.Integer(), nullable=False),
        sa.Column("purged_upstream", sa.Integer(), nullable=False),
        sa.Column("persisting", sa.Integer(), nullable=False),
        sa.Column("lag_rows", sa.BigInteger(), nullable=False),
        sa.Column("lag_seconds", sa.Double(), nullable=False),
        sa.Column("lag_alert", sa.Boolean(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["epoch_id"],
            ["db_epochs.epoch_id"],
            name=op.f("fk_reconcile_runs_epoch_id_db_epochs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reconcile_runs")),
    )
    op.create_index(
        "ix_reconcile_runs_service_started", "reconcile_runs", ["service", "started_at"]
    )


def downgrade() -> None:
    op.drop_table("reconcile_runs")
