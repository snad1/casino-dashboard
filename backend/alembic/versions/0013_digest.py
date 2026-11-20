"""Daily summary: the hour each user wants it, and which user and day were already handled.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user_channels", sa.Column("digest_hour", sa.SmallInteger(), nullable=True))
    op.create_check_constraint(
        "ck_user_channels_digest_hour",
        "user_channels",
        "digest_hour IS NULL OR (digest_hour >= 0 AND digest_hour <= 23)",
    )
    op.create_table(
        "digest_sends",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("outcome IN ('sent', 'skipped')", name=op.f("ck_digest_sends_outcome")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_digest_sends_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "service", "day", name=op.f("pk_digest_sends")),
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS digest_sends")
    op.execute("ALTER TABLE user_channels DROP CONSTRAINT IF EXISTS ck_user_channels_digest_hour")
    op.execute("ALTER TABLE user_channels DROP COLUMN IF EXISTS digest_hour")
