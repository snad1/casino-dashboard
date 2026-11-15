"""Sessions work through the request dependency, and each test's writes are rolled back."""

import uuid
from typing import Annotated

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session
from app.db.models import Service
from app.main import create_app
from app.settings import Settings

Session = Annotated[AsyncSession, Depends(get_session)]


def _service(name: str) -> Service:
    return Service(name=name, adapter="wolfbet", host="wolfbet.example.test")


@pytest.mark.parametrize("attempt", [1, 2])
async def test_writes_are_rolled_back_between_tests(db_session: AsyncSession, attempt: int) -> None:
    db_session.add(_service("wolfbet"))
    await db_session.commit()
    assert await db_session.scalar(select(Service.name)) == "wolfbet"


async def test_service_name_is_unique(db_session: AsyncSession) -> None:
    db_session.add(_service("goated"))
    await db_session.flush()
    db_session.add(_service("goated"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


def test_session_dependency_commits_and_rolls_back(migrated: Settings) -> None:
    name = f"route-{uuid.uuid4().hex}"
    app = create_app(migrated)

    @app.post("/services", status_code=204)
    async def add(session: Session) -> None:
        session.add(_service(name))
        await session.commit()

    @app.post("/services/fail")
    async def add_then_fail(session: Session) -> None:
        session.add(_service(f"{name}-failed"))
        await session.flush()
        raise RuntimeError("handler failed after a write")

    @app.get("/services")
    async def names(session: Session) -> list[str]:
        query = select(Service.name).where(Service.name.startswith(name))
        return list(await session.scalars(query))

    @app.delete("/services", status_code=204)
    async def cleanup(session: Session) -> None:
        await session.execute(delete(Service).where(Service.name.startswith(name)))
        await session.commit()

    with TestClient(app, raise_server_exceptions=False) as client:
        try:
            assert client.post("/services").status_code == 204
            assert client.post("/services/fail").status_code == 500
            assert client.get("/services").json() == [name]
        finally:
            client.delete("/services")
