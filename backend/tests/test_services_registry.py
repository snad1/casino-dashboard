"""Which services the deployment serves: each one's row exists once the api has started and is
never changed, an unknown name stops startup, and a service it doesn't serve is refused."""

from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import registry
from app.adapters.wolfbet import WolfBetAdapter
from app.adapters.wolfbet.control import ACTIONS
from app.alerts import digest
from app.auth.models import Role
from app.db.models import Service
from app.main import create_app
from app.settings import Settings
from tests.conftest import PUBLIC_HOST, make_client, run_in_session
from tests.test_ws_auth import _login

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


# ── which services this deployment serves ────────────────────────────────

DEMO = registry.ServiceEntry("demo", "Demo", WolfBetAdapter, ACTIONS)


@pytest.fixture
def demo(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second service the registry knows; the enabled list is put back after the test."""
    monkeypatch.setitem(registry.KNOWN, "demo", DEMO)
    monkeypatch.setattr(registry, "_enabled", registry.enabled())


def _with(settings: Settings, value: object) -> Settings:
    return Settings.model_validate({**settings.model_dump(), "dashboard_services": value})


def test_wolfbet_is_the_default_and_a_repeat_counts_once(settings: Settings) -> None:
    assert settings.dashboard_services == ["wolfbet"]
    assert _with(settings, "wolfbet, wolfbet").dashboard_services == ["wolfbet"]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("wolfbet,nosuch", "Unknown service in DASHBOARD_SERVICES: nosuch"),
        ("", "DASHBOARD_SERVICES needs at least one service"),
    ],
)
def test_a_bad_service_list_stops_startup(settings: Settings, value: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _with(settings, value)


def test_the_live_service_must_be_served(app_settings: Settings, demo: None) -> None:
    served = app_settings.model_copy(
        update={"dashboard_services": ["demo"], "live_service": WOLFBET}
    )
    with pytest.raises(ValueError, match="LIVE_SERVICE wolfbet is not in DASHBOARD_SERVICES"):
        create_app(served)


@pytest.mark.parametrize("path", ["/demo/control", "/demo/keys", "/demo/config", "/demo/analytics"])
def test_a_known_service_that_is_not_served_is_not_controllable(
    signed_in_as: Any, demo: None, path: str
) -> None:
    client = signed_in_as(Role.OWNER)
    response = client.get(path)
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "That action is not available for this service."


def test_a_served_service_gets_its_control_menu(
    control_settings: Settings, account: Any, demo: None
) -> None:
    served = control_settings.model_copy(update={"dashboard_services": [WOLFBET, "demo"]})
    with make_client(served) as client:
        _login(client, account.email)
        menu = client.get("/demo/control")
    assert menu.status_code == 200, menu.text
    assert [a["name"] for a in menu.json()["actions"]] == list(ACTIONS)


def test_every_served_service_gets_its_row(app_settings: Settings, demo: None) -> None:
    served = app_settings.model_copy(update={"dashboard_services": [WOLFBET, "demo"]})
    with make_client(served):
        pass

    async def names(session: AsyncSession) -> set[str]:
        return set(await session.scalars(select(Service.name)))

    assert {WOLFBET, "demo"} <= run_in_session(app_settings, names)


def test_digest_names_come_from_the_registry() -> None:
    assert digest.label(WOLFBET) == "WolfBet"
    assert digest.label("wolfbet-tg") == "Wolfbet-tg"
