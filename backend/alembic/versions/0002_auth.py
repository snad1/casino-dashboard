"""Auth: users, per-service roles, sessions, invites, password resets, TOTP, recovery codes.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_CHECK = "role IN ('owner', 'admin', 'operator', 'viewer')"


def _id() -> sa.Column[int]:
    return sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False)


def _user_id(table: str) -> sa.Column[int]:
    return sa.Column(
        "user_id",
        sa.BigInteger(),
        sa.ForeignKey("users.id", ondelete="CASCADE", name=op.f(f"fk_{table}_user_id_users")),
        nullable=False,
    )


def _ts(name: str, *, nullable: bool = True, default_now: bool = False) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.func.now() if default_now else None,
        nullable=nullable,
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.create_table(
        "users",
        _id(),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("disabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _ts("locked_until"),
        sa.Column(
            "created_by",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL", name=op.f("fk_users_created_by_users")),
            nullable=True,
        ),
        _ts("created_at", nullable=False, default_now=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "user_roles",
        _id(),
        _user_id("user_roles"),
        sa.Column(
            "service_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "services.id", ondelete="CASCADE", name=op.f("fk_user_roles_service_id_services")
            ),
            nullable=True,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_roles")),
        sa.UniqueConstraint(
            "user_id",
            "service_id",
            name=op.f("uq_user_roles_user_id"),
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint(ROLE_CHECK, name=op.f("ck_user_roles_role")),
    )
    op.create_index(
        "uq_user_roles_single_owner",
        "user_roles",
        ["role"],
        unique=True,
        postgresql_where=sa.text("role = 'owner'"),
    )
    op.create_table(
        "api_sessions",
        sa.Column("id_hash", sa.LargeBinary(), nullable=False),
        _user_id("api_sessions"),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        _ts("created_at", nullable=False, default_now=True),
        _ts("last_seen_at", nullable=False, default_now=True),
        _ts("step_up_at"),
        sa.PrimaryKeyConstraint("id_hash", name=op.f("pk_api_sessions")),
    )
    op.create_index(op.f("ix_api_sessions_user_id"), "api_sessions", ["user_id"])
    op.create_table(
        "invites",
        _id(),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "service_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "services.id", ondelete="CASCADE", name=op.f("fk_invites_service_id_services")
            ),
            nullable=True,
        ),
        sa.Column(
            "invited_by",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE", name=op.f("fk_invites_invited_by_users")),
            nullable=False,
        ),
        _ts("expires_at", nullable=False),
        _ts("accepted_at"),
        _ts("created_at", nullable=False, default_now=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invites")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_invites_token_hash")),
        sa.CheckConstraint(ROLE_CHECK, name=op.f("ck_invites_role")),
    )
    op.create_table(
        "password_resets",
        _id(),
        _user_id("password_resets"),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        _ts("expires_at", nullable=False),
        _ts("used_at"),
        _ts("created_at", nullable=False, default_now=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_resets")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_password_resets_token_hash")),
    )
    op.create_index(op.f("ix_password_resets_user_id"), "password_resets", ["user_id"])
    op.create_table(
        "totp",
        _user_id("totp"),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=False),
        _ts("enabled_at"),
        sa.Column("last_counter", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_totp")),
    )
    op.create_table(
        "recovery_codes",
        _id(),
        _user_id("recovery_codes"),
        sa.Column("code_hash", sa.Text(), nullable=False),
        _ts("used_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recovery_codes")),
    )
    op.create_index(op.f("ix_recovery_codes_user_id"), "recovery_codes", ["user_id"])


def downgrade() -> None:
    for table in (
        "recovery_codes",
        "totp",
        "password_resets",
        "invites",
        "api_sessions",
        "user_roles",
        "users",
    ):
        op.drop_table(table)
    op.execute("DROP EXTENSION IF EXISTS citext")
