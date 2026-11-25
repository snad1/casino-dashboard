"""The deployment's own service row exists once the api has started, and is never changed."""

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Service
from app.settings import Settings
from tests.conftest import PUBLIC_HOST, make_client, run_in_session

WOLFBET = "wolfbet"


def _rows(settings: Settings) -> list[str]:
    async def run(session: AsyncSession) -> list[str]:
        return list(await session.scalars(select(Service.host).where(Service.name == WOLFBET)))

    return run_in_session(settings, run)


def _started(settings: Settings) -> None:
    with make_client(settings.model_copy(update={"live_service": WOLFBET})):
        pass


def test_starting_the_api_registers_its_service(app_settings: Settings) -> None:
    async def drop(session: AsyncSession) -> None:
        await session.execute(delete(Service).where(Service.name == WOLFBET))
        await session.commit()

    run_in_session(app_settings, drop)
    _started(app_settings)
    assert _rows(app_settings) == [PUBLIC_HOST]


def test_a_restart_keeps_the_row_as_it_is(app_settings: Settings) -> None:
    _started(app_settings)

    async def rehost(session: AsyncSession) -> int:
        await session.execute(update(Service).where(Service.name == WOLFBET).values(host="kept"))
        await session.commit()
        return int(await session.scalar(select(func.count()).select_from(Service)) or 0)

    before = run_in_session(app_settings, rehost)
    _started(app_settings)
    assert _rows(app_settings) == ["kept"]

    async def count(session: AsyncSession) -> int:
        return int(await session.scalar(select(func.count()).select_from(Service)) or 0)

    assert run_in_session(app_settings, count) == before
