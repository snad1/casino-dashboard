"""A Telegram account reaches its own bot user and nothing else: every route and every topic.

Routes come from the api's own schema, so a new route is checked the day it is added.
"""

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.admin import TELEGRAM_ROLE
from app.auth.models import Role, User
from app.auth.permissions import FORBIDDEN
from app.main import create_app
from app.settings import Settings
from tests.conftest import run_in_session
from tests.test_ws_auth import _allowed
from tests.tg_links import TgUsers, new_uid

pytest_plugins = ["tests.fake_agent", "tests.control_rig", "tests.tg_rig"]

PARAM = re.compile(r"\{([^}]+)\}")
# Routes any signed-in account may use on itself, and the sign-in doors.
SELF = {
    ("GET", "/healthz"),
    ("POST", "/auth/login"),
    ("POST", "/auth/telegram"),
    ("POST", "/auth/logout"),
    ("POST", "/auth/logout-all"),
    ("GET", "/auth/me"),
    ("POST", "/auth/step-up"),
    ("POST", "/auth/totp/enrol"),
    ("POST", "/auth/totp/verify"),
    ("POST", "/auth/totp/disable"),
    ("POST", "/auth/invites/accept"),
    ("POST", "/auth/password-reset"),
    ("POST", "/auth/password-reset/confirm"),
    ("GET", "/alerts/me/channels"),
    ("PUT", "/alerts/me/channels"),
    ("POST", "/alerts/me/telegram-link"),
    ("POST", "/alerts/me/push-subscriptions"),
    ("POST", "/alerts/me/push-subscriptions/remove"),
    ("POST", "/ws-ticket"),
}
SAFE = {"GET", "HEAD", "OPTIONS"}


def _routes(settings: Settings) -> list[tuple[str, str]]:
    paths: dict[str, dict[str, Any]] = create_app(settings).openapi()["paths"]
    return sorted((method.upper(), path) for path, ops in paths.items() for method in ops)


def _url(path: str, uid: int) -> str:
    names = {"service": "wolfbet", "uid": str(uid), "action": "pause"}
    return PARAM.sub(lambda m: names.get(m.group(1), "1"), path)


def _call(client: TestClient, method: str, path: str, uid: int) -> Any:
    headers = {}
    if method not in SAFE:
        headers["X-CSRF-Token"] = client.get("/auth/me").json()["csrf_token"]
        headers["Idempotency-Key"] = "k" * 32
    return client.request(method, _url(path, uid), json={}, headers=headers)


def test_a_telegram_user_is_refused_every_route_but_their_own(
    tg_settings: Settings, tg_users: TgUsers
) -> None:
    mine, theirs, nobody = new_uid(), new_uid(), new_uid()
    tg_users.history(theirs)
    client = tg_users.client(mine)
    open_routes, leaked = [], []
    for method, path in _routes(tg_settings):
        if (method, path) in SELF or method == "HEAD":
            continue
        if "{uid}" in path:
            for other in (theirs, nobody):
                reply = _call(client, method, path, other)
                if reply.status_code != 403 or reply.json() != {"detail": FORBIDDEN}:
                    leaked.append((method, path, other, reply.status_code))
            if _call(client, method, path, mine).status_code in (401, 403):
                open_routes.append((method, path, "own uid refused"))
            continue
        status = _call(client, method, path, mine).status_code
        if status != 403:
            open_routes.append((method, path, status))
    assert leaked == []
    assert open_routes == []


@pytest.mark.parametrize(
    ("topic", "allowed"),
    [
        ("svc:wolfbet-tg:user:{mine}", True),
        ("svc:wolfbet-tg:user:{mine}:session:1", True),
        ("svc:wolfbet-tg:user:{theirs}", False),
        ("svc:wolfbet-tg:user:{theirs}:lane:1", False),
        ("svc:wolfbet", False),
        ("svc:wolfbet:system", False),
        ("svc:wolfbet-tg", False),
    ],
)
def test_a_telegram_user_follows_only_their_own_topics(
    tg_settings: Settings, tg_users: TgUsers, migrated: Settings, topic: str, allowed: bool
) -> None:
    mine, theirs = new_uid(), new_uid()
    tg_users.client(mine)

    async def account(db: AsyncSession) -> int:
        user_id = await db.scalar(select(User.id).where(User.tg_uid == mine))
        assert user_id is not None
        return user_id

    user_id = run_in_session(migrated, account)
    name = topic.format(mine=mine, theirs=theirs)
    assert _allowed(tg_settings, user_id, name) is allowed


@pytest.mark.parametrize(
    ("role", "two_factor", "expected"),
    [
        (Role.OWNER, True, 200),
        (Role.ADMIN, True, 200),
        (Role.ADMIN, False, 403),
        (Role.OPERATOR, True, 403),
        (Role.VIEWER, True, 403),
    ],
)
def test_owners_and_admins_reach_any_telegram_user_and_others_do_not(
    tg_users: TgUsers, signed_in_as: Any, role: Role, two_factor: bool, expected: int
) -> None:
    uid = new_uid()
    tg_users.history(uid)
    client = signed_in_as(role, two_factor=two_factor)
    assert client.get(f"/tg/{uid}/sessions").status_code == expected
    assert client.get("/tg/users").status_code == expected


def test_a_telegram_account_is_not_listed_or_promoted_by_user_admin(
    tg_users: TgUsers, signed_in_as: Any, migrated: Settings
) -> None:
    uid = new_uid()
    tg_users.client(uid)

    async def account(db: AsyncSession) -> int:
        user_id = await db.scalar(select(User.id).where(User.tg_uid == uid))
        assert user_id is not None
        return user_id

    user_id = run_in_session(migrated, account)
    admin = signed_in_as(Role.ADMIN)
    listed = [row["id"] for row in admin.get("/admin/users").json()["items"]]
    assert user_id not in listed
    csrf = {"X-CSRF-Token": admin.get("/auth/me").json()["csrf_token"]}
    reply = admin.put(f"/admin/users/{user_id}/role", json={"role": "admin"}, headers=csrf)
    assert reply.status_code == 403
    assert reply.json() == {"detail": TELEGRAM_ROLE}
