"""Control requests: one row per user and idempotency key, with the answer it got.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "control_requests",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("idem_key", sa.Text(), nullable=False),
        sa.Column("body_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("perm", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default="pending", nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("job_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("state IN ('pending', 'done')", name=op.f("ck_control_requests_state")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_control_requests_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_control_requests")),
        sa.UniqueConstraint("user_id", "idem_key", name=op.f("uq_control_requests_user_id")),
    )
    op.create_index(
        op.f("ix_control_requests_job_id"), "control_requests", ["job_id"], unique=False
    )
    op.create_index(
        op.f("ix_control_requests_created_at"), "control_requests", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS control_requests")
