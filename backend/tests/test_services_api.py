"""`GET /services`: the enabled services a user holds a role on, for the top-bar switcher."""

import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Role, User, UserRole
from app.auth.sessions import current_user
from app.db.models import Service
from app.live.aggregator import STALE_AFTER_S
from app.settings import Settings
from tests.conftest import RedisServer, make_client, make_user, run_in_session
from tests.fake_agent import FakeAgent, fail
from tests.test_live_services import (  # the demo fixture is used by name
    DEMO,
    WOLFBET,
    _one_lane,
    _publish_bets,
    _until,
    demo,  # noqa: F401
)

pytestmark = pytest.mark.usefixtures("demo")


@pytest.fixture
def served(control_settings: Settings) -> Settings:
    return control_settings.model_copy(
        update={"dashboard_services": [WOLFBET, DEMO], "live_service": WOLFBET}
    )


@pytest.fixture
def client_for(served: Settings) -> Iterator[Callable[[User], TestClient]]:
    clients: list[TestClient] = []

    def make(user: User) -> TestClient:
        client = make_client(served)
        client.app.dependency_overrides[current_user] = lambda: user  # type: ignore[attr-defined]
        clients.append(client.__enter__())
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def _on_service(settings: Settings, user: User, service: str, **narrow: Any) -> None:
    """Replace the user's global role with one on `service`, narrowed to a lane if asked."""

    async def run(session: AsyncSession) -> None:
        service_id = await session.scalar(select(Service.id).where(Service.name == service))
        await session.execute(
            update(UserRole)
            .where(UserRole.user_id == user.id)
            .values(service_id=service_id, **narrow)
        )
        await session.commit()

    run_in_session(settings, run)


def _names(client: TestClient) -> list[str]:
    response = client.get("/services")
    assert response.status_code == 200
    return [s["name"] for s in response.json()]


def test_signed_out_is_refused(served: Settings) -> None:
    with make_client(served) as client:
        assert client.get("/services").status_code == 401


def test_an_admin_sees_every_enabled_service_with_its_names(
    served: Settings, client_for: Callable[[User], TestClient]
) -> None:
    client = client_for(make_user(served, Role.ADMIN, two_factor=True))
    body = client.get("/services").json()
    assert [(s["name"], s["label"], s["ctl"]) for s in body] == [
        (WOLFBET, "WolfBet", "wolfctl"),
        (DEMO, "Demo", "democtl"),
    ]
    assert set(body[0]) == {"name", "label", "ctl", "installed", "live"}


@pytest.mark.parametrize("role", [Role.OPERATOR, Role.VIEWER])
def test_a_global_role_sees_every_service(
    role: Role, served: Settings, client_for: Callable[[User], TestClient]
) -> None:
    assert _names(client_for(make_user(served, role, two_factor=False))) == [WOLFBET, DEMO]


def test_a_role_on_one_service_sees_that_service_only(
    served: Settings, client_for: Callable[[User], TestClient]
) -> None:
    user = make_user(served, Role.VIEWER, two_factor=False)
    client = client_for(user)
    _on_service(served, user, WOLFBET)
    assert _names(client) == [WOLFBET]


def test_a_lane_role_counts_for_its_service(
    served: Settings, client_for: Callable[[User], TestClient]
) -> None:
    user = make_user(served, Role.OPERATOR, two_factor=False)
    client = client_for(user)
    _on_service(served, user, DEMO, lane=3)
    assert _names(client) == [DEMO]


@pytest.mark.parametrize("role", [Role.TG_USER, Role.LANE_MANAGER])
def test_no_service_through_a_global_tg_or_lane_manager_role(
    role: Role, served: Settings, client_for: Callable[[User], TestClient]
) -> None:
    assert _names(client_for(make_user(served, role, two_factor=False))) == []


def test_a_disabled_user_sees_nothing(
    served: Settings, client_for: Callable[[User], TestClient]
) -> None:
    user = make_user(served, Role.ADMIN, two_factor=True)
    user.disabled = True
    assert _names(client_for(user)) == []


def _demo_not_installed(request: dict[str, Any]) -> list[dict[str, Any]]:
    """The agent drives WolfBet; it has no demo bot, as when democtl is missing."""
    if request["cmd"].startswith(f"{DEMO}."):
        return [fail(request, "unknown_command")]
    return _one_lane(request)


def test_installed_and_live_come_from_each_services_pipeline(
    fake_agent: FakeAgent,
    redis_server: RedisServer,
    served: Settings,
    client_for: Callable[[User], TestClient],
) -> None:
    fake_agent.reply = _demo_not_installed
    client = client_for(make_user(served, Role.ADMIN, two_factor=True))
    _publish_bets(redis_server, 1)

    def body() -> dict[str, Any]:
        return {s["name"]: s for s in client.get("/services").json()}

    assert _until(lambda: body()[WOLFBET]["live"] and body()[DEMO]["installed"] is False)
    assert body()[WOLFBET]["installed"] is True
    assert body()[DEMO]["live"] is False, "the demo stream is refused, so nothing arrives"


def test_a_quiet_stream_is_not_live(
    served: Settings, client_for: Callable[[User], TestClient]
) -> None:
    client = client_for(make_user(served, Role.ADMIN, two_factor=True))
    pipelines = {p.svc: p for p in client.app.state.pipelines}  # type: ignore[attr-defined]
    pipelines[WOLFBET].consumer.last_event_ms = (time.time() - STALE_AFTER_S - 1) * 1000
    assert client.get("/services").json()[0]["live"] is False


def test_with_live_off_nothing_is_known_or_live(served: Settings) -> None:
    off = served.model_copy(update={"live_service": None})
    user = make_user(off, Role.ADMIN, two_factor=True)
    with make_client(off) as client:
        client.app.dependency_overrides[current_user] = lambda: user  # type: ignore[attr-defined]
        body = client.get("/services").json()
    assert [(s["installed"], s["live"]) for s in body] == [(None, False), (None, False)]
