"""The update notice (`GET /{service}/update`) and the owner's auto-update switch."""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Role
from app.db.models import Service
from app.settings import Settings
from tests.conftest import run_in_session
from tests.fake_agent import FakeAgent, default_reply, fail, ok

BEHIND = {"behind": 3, "head": "abc1234", "latest": "def5678"}


def _behind(request: dict[str, Any]) -> list[dict[str, Any]]:
    if request["cmd"] == "wolfbet.update_check":
        return [ok(request, BEHIND)]
    return default_reply(request)


@pytest.fixture
def behind(fake_agent: FakeAgent) -> FakeAgent:
    fake_agent.reply = _behind
    return fake_agent


def _csrf(client: TestClient) -> dict[str, str]:
    return {"x-csrf-token": client.get("/auth/me").json()["csrf_token"]}


def auto_update(settings: Settings, service: str = "wolfbet") -> bool:
    async def run(session: AsyncSession) -> bool:
        value = await session.scalar(select(Service.auto_update).where(Service.name == service))
        return bool(value)

    return run_in_session(settings, run)


def test_the_notice_says_how_far_behind_and_asks_git_once_a_while(
    behind: FakeAgent, signed_in_as: Callable[..., TestClient]
) -> None:
    client = signed_in_as(Role.VIEWER, two_factor=False)
    first = client.get("/wolfbet/update")
    assert first.status_code == 200
    assert first.json() == {**BEHIND, "auto_update": False}
    assert client.get("/wolfbet/update").json() == first.json()
    assert behind.cmds().count("wolfbet.update_check") == 1


def test_a_failed_check_says_nothing_and_is_asked_again(
    fake_agent: FakeAgent, signed_in_as: Callable[..., TestClient]
) -> None:
    fake_agent.reply = lambda request: (
        [fail(request, "timeout")]
        if request["cmd"].endswith(".update_check")
        else default_reply(request)
    )
    client = signed_in_as(Role.VIEWER, two_factor=False)
    for _ in range(2):
        body = client.get("/wolfbet/update").json()
        assert (body["behind"], body["head"], body["latest"]) == (None, None, None)
    assert fake_agent.cmds().count("wolfbet.update_check") == 2


def test_a_service_this_deployment_does_not_serve_is_not_found(
    behind: FakeAgent, signed_in_as: Callable[..., TestClient]
) -> None:
    client = signed_in_as(Role.OWNER)
    assert client.get("/goated/update").status_code == 404
    assert "goated.update_check" not in behind.cmds()


def test_the_owner_switches_auto_update_with_a_fresh_code(
    behind: FakeAgent, signed_in_as: Callable[..., TestClient], control_settings: Settings
) -> None:
    client = signed_in_as(Role.OWNER)
    reply = client.put("/wolfbet/update/auto", json={"enabled": True}, headers=_csrf(client))
    assert reply.status_code == 200
    assert reply.json()["auto_update"] is True
    assert auto_update(control_settings) is True
    assert client.get("/wolfbet/update").json()["auto_update"] is True
    off = client.put("/wolfbet/update/auto", json={"enabled": False}, headers=_csrf(client))
    assert off.json()["auto_update"] is False
    assert auto_update(control_settings) is False


def test_an_admin_may_not_switch_auto_update(
    behind: FakeAgent, signed_in_as: Callable[..., TestClient], control_settings: Settings
) -> None:
    client = signed_in_as(Role.ADMIN)
    reply = client.put("/wolfbet/update/auto", json={"enabled": True}, headers=_csrf(client))
    assert reply.status_code == 403
    assert auto_update(control_settings) is False


def test_the_owner_without_a_fresh_code_is_asked_for_one(
    behind: FakeAgent, signed_in_as: Callable[..., TestClient], control_settings: Settings
) -> None:
    client = signed_in_as(Role.OWNER, two_factor=False)
    reply = client.put("/wolfbet/update/auto", json={"enabled": True}, headers=_csrf(client))
    assert reply.status_code == 403
    assert auto_update(control_settings) is False
