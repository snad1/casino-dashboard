"""The baseline migration runs up and down against real Timescale and matches the models."""

import asyncio
from collections.abc import Callable

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.auth.models  # registers the auth tables on Base.metadata
import app.mail.models  # noqa: F401  # registers the outbox on Base.metadata
from app.db.models import Base
from app.settings import Settings
from tests.conftest import alembic_config


def _on_connection[T](settings: Settings, check: Callable[[Connection], T]) -> T:
    async def run() -> T:
        engine = create_async_engine(settings.database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(check)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _tables(connection: Connection) -> set[str]:
    return set(inspect(connection).get_table_names())


def _has_timescale(connection: Connection) -> bool:
    query = text("SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'")
    count: int = connection.execute(query).scalar_one()
    return count == 1


def _drift(connection: Connection) -> list[object]:
    return list(compare_metadata(MigrationContext.configure(connection), Base.metadata))


def test_baseline_creates_services_and_timescale(migrated: Settings) -> None:
    def describe(connection: Connection) -> tuple[dict[str, bool], list[str | None]]:
        inspector = inspect(connection)
        columns = {c["name"]: c["nullable"] for c in inspector.get_columns("services")}
        unique = [u["name"] for u in inspector.get_unique_constraints("services")]
        return columns, unique

    columns, unique = _on_connection(migrated, describe)
    assert columns == {
        "id": False,
        "name": False,
        "adapter": False,
        "host": False,
        "created_at": False,
    }
    assert unique == ["uq_services_name"]
    assert _on_connection(migrated, _has_timescale)


def test_round_trip_down_and_up(migrated: Settings) -> None:
    config = alembic_config(migrated)
    command.downgrade(config, "base")
    assert "services" not in _on_connection(migrated, _tables)
    command.upgrade(config, "head")
    assert "services" in _on_connection(migrated, _tables)


def test_models_match_migrations(migrated: Settings) -> None:
    assert _on_connection(migrated, _drift) == []


AUTH_TABLES = {
    "users",
    "user_roles",
    "api_sessions",
    "invites",
    "password_resets",
    "totp",
    "recovery_codes",
}


def test_auth_round_trip_keeps_services(migrated: Settings) -> None:
    config = alembic_config(migrated)
    command.downgrade(config, "0001")
    tables = _on_connection(migrated, _tables)
    assert "services" in tables
    assert not AUTH_TABLES & tables
    command.upgrade(config, "head")
    assert AUTH_TABLES <= _on_connection(migrated, _tables)
