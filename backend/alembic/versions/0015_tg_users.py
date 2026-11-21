"""Telegram users sign in with a one-time link from the bot: their account and used link ids.

A Telegram account has no email and no password; `tg_uid` ties it to the bot user whose data
it may see. Each link id is recorded once used, so a link signs in only once.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ROLES = "role IN ('owner', 'admin', 'operator', 'viewer')"
EMAIL_OR_TG_UID = "email IS NOT NULL OR tg_uid IS NOT NULL"
NEW_ROLES = "role IN ('owner', 'admin', 'operator', 'viewer', 'tg_user')"


def _roles(check: str) -> None:
    for table in ("user_roles", "invites"):
        op.drop_constraint(op.f(f"ck_{table}_role"), table, type_="check")
        op.create_check_constraint("role", table, check)


def upgrade() -> None:
    op.add_column("users", sa.Column("tg_uid", sa.BigInteger(), nullable=True))
    op.create_unique_constraint(op.f("uq_users_tg_uid"), "users", ["tg_uid"])
    op.alter_column("users", "email", nullable=True)
    op.create_check_constraint("email_or_tg_uid", "users", EMAIL_OR_TG_UID)
    _roles(NEW_ROLES)
    op.create_table(
        "tg_login_tokens",
        sa.Column("id_hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "used_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id_hash", name=op.f("pk_tg_login_tokens")),
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM users WHERE tg_uid IS NOT NULL) THEN"
        " RAISE EXCEPTION 'Telegram accounts exist; disable and delete them first'; END IF;"
        " END $$"
    )
    op.drop_table("tg_login_tokens")
    _roles(OLD_ROLES)
    op.drop_constraint(op.f("ck_users_email_or_tg_uid"), "users", type_="check")
    op.alter_column("users", "email", nullable=False)
    op.drop_constraint(op.f("uq_users_tg_uid"), "users", type_="unique")
    op.drop_column("users", "tg_uid")
