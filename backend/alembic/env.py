"""Run migrations on the async engine; the URL comes from settings, never from alembic.ini."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.audit  # registers the audit log on Base.metadata
import app.auth.models  # registers the auth tables on Base.metadata
import app.mail.models  # noqa: F401  # registers the outbox on Base.metadata
from app.db.models import Base
from app.settings import load_migration_settings

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)


APP_ROLE = "dashboard_app"


def _set_app_password(connection: Connection, password: str) -> None:
    """Postgres quotes the password itself (`%L`), so no value is ever spliced into SQL here."""
    exists = connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": APP_ROLE}
    ).scalar()
    if not exists:
        return
    statement = connection.execute(
        text(
            "SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L',"
            " CAST(:role AS text), CAST(:password AS text))"
        ),
        {"role": APP_ROLE, "password": password},
    ).scalar_one()
    connection.exec_driver_sql(statement)


def _run(connection: Connection, app_password: str | None) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
        if app_password:
            _set_app_password(connection, app_password)


async def _run_online() -> None:
    url: str | None = config.attributes.get("url")
    app_password: str | None = config.attributes.get("app_password")
    if url is None:
        settings = load_migration_settings()
        url = settings.database_url
        if settings.postgres_app_password is not None:
            app_password = settings.postgres_app_password.get_secret_value()
    engine = create_async_engine(url, poolclass=NullPool, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run, app_password)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline SQL generation is not supported; run against a database.")
asyncio.run(_run_online())
