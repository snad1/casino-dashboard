"""The services row each deployment needs before any role can be scoped to it."""

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = structlog.get_logger("app.db.services")


async def register_service(
    sessionmaker: async_sessionmaker[AsyncSession], name: str, host: str
) -> None:
    """Add the service this deployment serves, once: roles on its lanes and keys refer to
    the row, and an empty database had none. An existing row is left as it is."""
    try:
        async with sessionmaker() as session:
            await session.execute(
                text(
                    "INSERT INTO services (name, adapter, host) VALUES (:name, :name, :host)"
                    " ON CONFLICT (name) DO NOTHING"
                ),
                {"name": name, "host": host},
            )
            await session.commit()
    except Exception:
        log.exception("could not register the service; roles on it are refused until it is")
