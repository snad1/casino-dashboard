"""Comments on sessions and bets, and notes pinned to a time on the chart.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "comments",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("epoch_id", sa.BigInteger(), nullable=True),
        sa.Column("session_src_id", sa.BigInteger(), nullable=True),
        sa.Column("bet_src_id", sa.BigInteger(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('session', 'bet', 'chart')", name=op.f("ck_comments_kind")),
        sa.CheckConstraint(
            "char_length(body) BETWEEN 1 AND 2000", name=op.f("ck_comments_body_length")
        ),
        sa.CheckConstraint(
            "(kind = 'session' AND epoch_id IS NOT NULL AND session_src_id IS NOT NULL"
            " AND bet_src_id IS NULL AND at IS NULL)"
            " OR (kind = 'bet' AND epoch_id IS NOT NULL AND bet_src_id IS NOT NULL"
            " AND session_src_id IS NULL AND at IS NULL)"
            " OR (kind = 'chart' AND at IS NOT NULL AND epoch_id IS NULL"
            " AND session_src_id IS NULL AND bet_src_id IS NULL)",
            name=op.f("ck_comments_target"),
        ),
        sa.ForeignKeyConstraint(
            ["epoch_id"], ["db_epochs.epoch_id"], name=op.f("fk_comments_epoch_id_db_epochs")
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name=op.f("fk_comments_author_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comments")),
    )
    op.create_index(
        "ix_comments_session", "comments", ["service", "kind", "epoch_id", "session_src_id"]
    )
    op.create_index("ix_comments_bet", "comments", ["service", "kind", "epoch_id", "bet_src_id"])
    op.create_index("ix_comments_at", "comments", ["service", "at"])


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS comments")
