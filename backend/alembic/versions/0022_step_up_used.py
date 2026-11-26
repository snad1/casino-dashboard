"""A checked code covers protected actions for a day after the last one, up to a week.

`api_sessions.step_up_used_at` is when the code last allowed a protected action;
`step_up_at` stays the time the code was entered, which the weekly limit counts from.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_sessions", sa.Column("step_up_used_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("api_sessions", "step_up_used_at")
