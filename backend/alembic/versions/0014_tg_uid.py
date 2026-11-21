"""The Telegram user a database file belongs to (one file per user in the TG bot).

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("db_epochs", sa.Column("tg_uid", sa.BigInteger(), nullable=True))
    op.create_index("ix_db_epochs_service_tg_uid", "db_epochs", ["service", "tg_uid"])


def downgrade() -> None:
    op.drop_index("ix_db_epochs_service_tg_uid", table_name="db_epochs")
    op.drop_column("db_epochs", "tg_uid")
