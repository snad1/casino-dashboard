"""A lane_manager role: a friend who creates and runs their own lanes.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ROLES = "role IN ('owner', 'admin', 'operator', 'viewer', 'tg_user')"
NEW_ROLES = "role IN ('owner', 'admin', 'operator', 'viewer', 'tg_user', 'lane_manager')"


def _roles(check: str) -> None:
    for table in ("user_roles", "invites"):
        op.drop_constraint(op.f(f"ck_{table}_role"), table, type_="check")
        op.create_check_constraint("role", table, check)


def upgrade() -> None:
    _roles(NEW_ROLES)


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM user_roles WHERE role = 'lane_manager')"
        " OR EXISTS (SELECT 1 FROM invites WHERE role = 'lane_manager') THEN"
        " RAISE EXCEPTION 'lane_manager roles exist; remove them first'; END IF; END $$"
    )
    _roles(OLD_ROLES)
