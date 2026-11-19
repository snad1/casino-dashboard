"""User admin: managers with a fresh second factor list, re-role, disable and enable accounts."""

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.admin import NOT_YOURSELF, OWNER_LOCKED, USER_NOT_FOUND
from app.auth.models import ApiSession, Role, User, UserRole
from app.auth.stepup import STEP_UP_NEEDED
from app.settings import Settings
from tests.conftest import Account, make_user, run_in_session
from tests.test_totp import _enrol


def _make_admin(settings: Settings, user_id: int) -> None:
    async def run(session: AsyncSession) -> None:
        await session.execute(
            update(UserRole).where(UserRole.user_id == user_id).values(role=Role.ADMIN)
        )
        await session.commit()

    run_in_session(settings, run)


@pytest.fixture
def admin(app_settings: Settings, client: TestClient, account: Account) -> TestClient:
    """The signed-in account as an admin whose 2FA was just confirmed."""
    _make_admin(app_settings, account.id)
    _enrol(client, account.email)
    return client


@pytest.fixture
def owner(app_settings: Settings) -> Iterator[User]:
    async def existing(session: AsyncSession) -> User | None:
        query = select(User).join(UserRole).where(UserRole.role == Role.OWNER)
        found: User | None = await session.scalar(query)
        return found

    found = run_in_session(app_settings, existing)
    if found is not None:
        yield found
        return
    created = make_user(app_settings, Role.OWNER, two_factor=True)
    yield created

    async def remove(session: AsyncSession) -> None:
        await session.execute(delete(User).where(User.id == created.id))
        await session.commit()

    run_in_session(app_settings, remove)


def _send(
    client: TestClient, method: str, path: str, body: Mapping[str, object] | None = None
) -> int:
    token = client.get("/auth/me").json()["csrf_token"]
    response = client.request(method, path, json=body, headers={"x-csrf-token": token})
    return response.status_code


def _user(settings: Settings, user_id: int) -> tuple[bool, list[tuple[str | None, str]]]:
    async def run(session: AsyncSession) -> tuple[bool, list[tuple[str | None, str]]]:
        user = await session.get(User, user_id)
        assert user is not None
        rows = await session.execute(
            select(UserRole.service_id, UserRole.role).where(UserRole.user_id == user_id)
        )
        return user.disabled, [(str(s) if s else None, str(r)) for s, r in rows]

    return run_in_session(settings, run)


def test_managers_list_every_user_with_roles_and_2fa(
    app_settings: Settings, admin: TestClient, account: Account
) -> None:
    other = make_user(app_settings, Role.OPERATOR, two_factor=True)
    response = admin.get("/admin/users")
    assert response.status_code == 200
    users = {u["id"]: u for u in response.json()["items"]}
    assert users[account.id]["roles"] == [{"service": None, "role": "admin"}]
    assert users[account.id]["totp_enabled"] is True
    assert users[other.id]["roles"] == [{"service": None, "role": "operator"}]
    assert set(users[other.id]) == {
        "id",
        "email",
        "roles",
        "disabled",
        "totp_enabled",
        "created_at",
    }


def test_viewers_cannot_see_or_change_users(app_settings: Settings, as_user: object) -> None:
    viewer = as_user(make_user(app_settings, Role.VIEWER, two_factor=False))  # type: ignore[operator]
    assert viewer.get("/admin/users").status_code == 403


def test_changes_need_a_fresh_second_factor(
    app_settings: Settings, admin: TestClient, account: Account
) -> None:
    target = make_user(app_settings, Role.VIEWER, two_factor=False)

    async def age(session: AsyncSession) -> None:
        old = datetime.now(UTC) - timedelta(minutes=6)
        query = update(ApiSession).where(ApiSession.user_id == account.id)
        await session.execute(query.values(step_up_at=old))
        await session.commit()

    run_in_session(app_settings, age)
    token = admin.get("/auth/me").json()["csrf_token"]
    response = admin.post(f"/admin/users/{target.id}/disable", headers={"x-csrf-token": token})
    assert response.status_code == 403
    assert response.json() == {"detail": STEP_UP_NEEDED, "step_up_required": True}


def test_role_change_replaces_every_role(app_settings: Settings, admin: TestClient) -> None:
    target = make_user(app_settings, Role.VIEWER, two_factor=False)
    assert _send(admin, "PUT", f"/admin/users/{target.id}/role", {"role": "operator"}) == 204
    assert _user(app_settings, target.id) == (False, [(None, "operator")])
    owner_grant = _send(admin, "PUT", f"/admin/users/{target.id}/role", {"role": "owner"})
    assert owner_grant == 422
    unknown = {"role": "viewer", "service": "no-such-service"}
    assert _send(admin, "PUT", f"/admin/users/{target.id}/role", unknown) == 422


def test_disable_ends_sessions_and_enable_restores_the_account(
    app_settings: Settings, admin: TestClient
) -> None:
    target = make_user(app_settings, Role.VIEWER, two_factor=False)

    async def add_session(session: AsyncSession) -> None:
        session.add(
            ApiSession(
                id_hash=b"x" * 32,
                user_id=target.id,
            )
        )
        await session.commit()

    async def sessions(session: AsyncSession) -> int:
        rows = await session.scalars(select(ApiSession).where(ApiSession.user_id == target.id))
        return len(list(rows))

    run_in_session(app_settings, add_session)
    assert _send(admin, "POST", f"/admin/users/{target.id}/disable") == 204
    assert _user(app_settings, target.id)[0] is True
    assert run_in_session(app_settings, sessions) == 0
    assert _send(admin, "POST", f"/admin/users/{target.id}/enable") == 204
    assert _user(app_settings, target.id)[0] is False


def test_the_owner_yourself_and_unknown_users_are_refused(
    admin: TestClient, account: Account, owner: User
) -> None:
    for path in (f"/admin/users/{owner.id}/disable", f"/admin/users/{account.id}/disable"):
        token = admin.get("/auth/me").json()["csrf_token"]
        response = admin.post(path, headers={"x-csrf-token": token})
        assert response.status_code == 403
        assert response.json()["detail"] in {OWNER_LOCKED, NOT_YOURSELF}
    token = admin.get("/auth/me").json()["csrf_token"]
    missing = admin.post("/admin/users/999999999/enable", headers={"x-csrf-token": token})
    assert missing.status_code == 404
    assert missing.json()["detail"] == USER_NOT_FOUND
