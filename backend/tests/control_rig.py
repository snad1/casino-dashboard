"""Fixtures for the control and config api tests: an api wired to the fake agent, signed in."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Role, UserRole
from app.settings import Settings
from tests.conftest import Account, make_client, run_in_session
from tests.fake_agent import TOKEN, FakeAgent
from tests.test_totp import _enrol
from tests.test_ws_auth import _login


def set_role(settings: Settings, user_id: int, role: Role) -> None:
    async def run(session: AsyncSession) -> None:
        await session.execute(update(UserRole).where(UserRole.user_id == user_id).values(role=role))
        await session.commit()

    run_in_session(settings, run)


@pytest.fixture
def control_settings(app_settings: Settings, fake_agent: FakeAgent) -> Settings:
    return app_settings.model_copy(
        update={"agent_socket": fake_agent.path, "agent_token": SecretStr(TOKEN)}
    )


@pytest.fixture
def signed_in_as(control_settings: Settings, account: Account) -> Iterator[Any]:
    """The account signed in with `role`; with 2FA turned on and just confirmed if asked."""
    clients: list[TestClient] = []

    def make(role: Role, *, two_factor: bool = True) -> TestClient:
        set_role(control_settings, account.id, role)
        client = make_client(control_settings).__enter__()
        clients.append(client)
        if two_factor:
            _enrol(client, account.email)
        else:
            _login(client, account.email)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)
