"""A role limited to vault keys: the lanes betting with the key now, and that key's history."""

import secrets
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.lane_access import NOT_YOUR_KEY
from app.auth.admin import KEY_UNKNOWN
from app.auth.invites import INVITE_INVALID
from app.auth.key_lanes import KEY_LANES
from app.auth.models import Invite, Role, User, UserRole
from app.auth.permissions import FORBIDDEN
from app.db.models import Service
from app.settings import Settings
from tests.conftest import NO_LOGIN, Account, new_email, run_in_session
from tests.fake_agent import FakeAgent, fail, ok
from tests.test_history_api import T0, Seed
from tests.test_lane_invites import _accept, _send

WOLFBET = "wolfbet"
_created: list[int] = []


@pytest.fixture(autouse=True)
def _drop_key_data(app_settings: Settings) -> Iterator[None]:
    """Remove key users and key invites, so the migration round trip (which refuses them)
    still runs after this file."""
    yield

    async def drop(session: AsyncSession) -> None:
        await session.execute(delete(User).where(User.id.in_(_created)))
        await session.execute(delete(UserRole).where(UserRole.key_ref.is_not(None)))
        await session.execute(delete(Invite).where(func.cardinality(Invite.key_refs) > 0))
        await session.commit()

    run_in_session(app_settings, drop)
    _created.clear()


@pytest.fixture
def vault(fake_agent: FakeAgent) -> dict[str, list[int]]:
    """The bot's keys and the lanes using each; edit it to move a lane between keys."""
    keys = {"default": [1], "lakai": [8, 9], "other": [2]}

    def reply(request: dict[str, Any]) -> list[dict[str, Any]]:
        cmd = request["cmd"]
        if cmd.endswith(".key_list"):
            rows = [
                {"id": k, "kind": "key", "owner_user_id": None, "used_by_lanes": lanes}
                for k, lanes in keys.items()
            ]
            rows.append({"id": "relay", "kind": "proxy", "used_by_lanes": [8]})
            return [ok(request, {"exit_code": 0, "stderr": "", "json": {"v": 1, "keys": rows}})]
        if cmd.endswith(".lane_ids"):
            return [ok(request, {"exit_code": 0, "stdout": "1 2 8 9\n", "stderr": ""})]
        return [ok(request, {"exit_code": 0, "stdout": "", "stderr": "", "json": {"v": 1}})]

    fake_agent.reply = reply
    return keys


def _key_user(settings: Settings, service: str, *keys: str, role: Role = Role.VIEWER) -> User:
    """A user whose only roles are `role` on each of `keys` of `service`."""

    async def run(session: AsyncSession) -> User:
        await session.execute(
            text(
                "INSERT INTO services (name, adapter, host) VALUES (:n, 'wolfbet', :h)"
                " ON CONFLICT (name) DO NOTHING"
            ),
            {"n": service, "h": f"{service}.test"},
        )
        service_id = await session.scalar(
            text("SELECT id FROM services WHERE name = :n"), {"n": service}
        )
        user = User(email=new_email(), password_hash=NO_LOGIN)
        session.add(user)
        await session.flush()
        _created.append(user.id)
        session.add_all(
            UserRole(user_id=user.id, service_id=service_id, role=role, key_ref=key) for key in keys
        )
        await session.commit()
        return user

    return run_in_session(settings, run)


def _signed_in_on_keys(
    settings: Settings, signed_in_as: Any, account: Account, *keys: str, role: Role
) -> TestClient:
    """The account signed in, its roles then replaced by `role` on each of `keys`."""
    client: TestClient = signed_in_as(role)

    async def run(session: AsyncSession) -> None:
        await session.execute(
            text(
                "INSERT INTO services (name, adapter, host) VALUES ('wolfbet', 'wolfbet', 'h')"
                " ON CONFLICT (name) DO NOTHING"
            )
        )
        service_id = await session.scalar(select(Service.id).where(Service.name == WOLFBET))
        await session.execute(delete(UserRole).where(UserRole.user_id == account.id))
        session.add_all(
            UserRole(user_id=account.id, service_id=service_id, role=role, key_ref=key)
            for key in keys
        )
        await session.commit()

    run_in_session(settings, run)
    return client


def _post(client: TestClient, action: str, body: dict[str, Any]) -> Any:
    token = client.get("/auth/me").json()["csrf_token"]
    headers = {"x-csrf-token": token, "idempotency-key": secrets.token_urlsafe(24)}
    return client.post(f"/{WOLFBET}/control/{action}", json=body, headers=headers)


def _refused(response: Any) -> bool:
    return bool(response.status_code == 403 and response.json()["detail"] == FORBIDDEN)


def test_a_key_role_opens_the_lanes_on_that_key_and_nothing_service_wide(
    control_settings: Settings, signed_in_as: Any, account: Account, vault: dict[str, list[int]]
) -> None:
    client = _signed_in_on_keys(
        control_settings, signed_in_as, account, "lakai", role=Role.OPERATOR
    )
    roles = client.get("/auth/me").json()["roles"]
    assert [(r["lane"], r["key_ref"]) for r in roles] == [(8, "lakai"), (9, "lakai")]
    assert _post(client, "lane_stop", {"lane": 9}).status_code == 200
    assert _post(client, "lane_stop", {"lane": 1}).status_code == 403
    assert _refused(client.get(f"/{WOLFBET}/ingest"))
    on_other_key = {"changes": [{"key": "key_ref", "value": "other", "lane": 9}]}
    response = _post(client, "config_set", on_other_key)
    assert (response.status_code, response.json()["detail"]) == (403, NOT_YOUR_KEY)


def test_access_follows_a_lane_moved_off_the_key(
    control_settings: Settings, signed_in_as: Any, account: Account, vault: dict[str, list[int]]
) -> None:
    client = _signed_in_on_keys(
        control_settings, signed_in_as, account, "lakai", role=Role.OPERATOR
    )
    assert _post(client, "lane_stop", {"lane": 8}).status_code == 200
    vault["lakai"] = [9]
    vault["default"] = [1, 8]
    KEY_LANES.forget(WOLFBET)
    assert _post(client, "lane_stop", {"lane": 8}).status_code == 403
    assert [r["lane"] for r in client.get("/auth/me").json()["roles"]] == [9]


def test_a_host_that_cannot_say_opens_no_lane(
    control_settings: Settings, signed_in_as: Any, account: Account, fake_agent: FakeAgent
) -> None:
    fake_agent.reply = lambda request: [fail(request, "unavailable")]
    client = _signed_in_on_keys(
        control_settings, signed_in_as, account, "lakai", role=Role.OPERATOR
    )
    roles = client.get("/auth/me").json()["roles"]
    assert [(r["lane"], r["key_ref"]) for r in roles] == [(None, "lakai")]
    assert _post(client, "lane_stop", {"lane": 9}).status_code == 403


def test_history_keeps_to_the_sessions_that_bet_with_the_key(
    control_settings: Settings, client_as: Any, vault: dict[str, list[int]]
) -> None:
    seed = Seed(control_settings)
    try:
        epoch = seed.epoch()
        for src_id, lane, key in ((1, 8, "lakai"), (2, 8, None), (3, 9, "lakai"), (4, 2, "other")):
            seed.session(epoch, src_id, lane=lane, key_ref=key)
            seed.bet(epoch, src_id, T0 + timedelta(minutes=src_id), session=src_id, lane=lane)
        client = client_as(_key_user(control_settings, seed.service, "lakai"))
        page = client.get(f"/{seed.service}/sessions").json()
        assert ([s["id"] for s in page["items"]], page["total"]) == ([3, 1], 2)
        lane8 = client.get(f"/{seed.service}/sessions", params={"lane": 8}).json()["items"]
        assert [s["id"] for s in lane8] == [1]
        assert _refused(client.get(f"/{seed.service}/sessions", params={"key": "other"}))
        assert client.get(f"/{seed.service}/sessions/{epoch}/1").status_code == 200
        assert client.get(f"/{seed.service}/sessions/{epoch}/2").status_code == 404
        series = client.get(
            f"/{seed.service}/series",
            params={"from": T0.isoformat(), "to": (T0 + timedelta(minutes=10)).isoformat()},
        ).json()
        assert sorted(s["lane"] for s in series["series"]) == [8, 9]
        points = {s["lane"]: len(s["points"]) for s in series["series"]}
        assert points == {8: 1, 9: 1}
    finally:

        async def drop(session: AsyncSession) -> None:
            await session.execute(text("DELETE FROM services WHERE name = :n"), {"n": seed.service})
            await session.commit()

        seed.clean()
        run_in_session(control_settings, drop)


def test_analytics_for_a_key_come_from_history(
    control_settings: Settings, client_as: Any, fake_agent: FakeAgent, vault: dict[str, list[int]]
) -> None:
    client = client_as(_key_user(control_settings, WOLFBET, "lakai"))
    response = client.get(f"/{WOLFBET}/analytics")
    assert (response.status_code, response.json()["source"]) == (200, "history")
    assert "wolfbet.analytics" not in fake_agent.cmds()
    assert _refused(client.get(f"/{WOLFBET}/analytics", params={"key": "other"}))


def test_the_users_page_gives_a_role_on_keys(
    control_settings: Settings, signed_in_as: Any, vault: dict[str, list[int]]
) -> None:
    admin = signed_in_as(Role.ADMIN)
    target = _key_user(control_settings, WOLFBET)
    path = f"/admin/users/{target.id}/role"
    body = {"role": "viewer", "service": WOLFBET, "keys": ["other", "lakai"]}
    assert _send(admin, "PUT", path, body).status_code == 204
    listed = {u["id"]: u for u in admin.get("/admin/users").json()["items"]}
    assert [(r["role"], r["lane"], r["key_ref"]) for r in listed[target.id]["roles"]] == [
        ("viewer", None, "lakai"),
        ("viewer", None, "other"),
    ]
    unknown = _send(admin, "PUT", path, {**body, "keys": ["nope"]})
    assert (unknown.status_code, unknown.json()["detail"]) == (422, KEY_UNKNOWN)


@pytest.mark.parametrize(
    "scope",
    [
        {"role": "viewer", "service": WOLFBET, "keys": ["lakai"], "lanes": [8]},
        {"role": "admin", "service": WOLFBET, "keys": ["lakai"]},
        {"role": "lane_manager", "service": WOLFBET, "keys": ["lakai"]},
        {"role": "viewer", "keys": ["lakai"]},
        {"role": "viewer", "service": WOLFBET, "keys": ["lakai", "lakai"]},
        {"role": "viewer", "service": WOLFBET, "keys": [f"k{n}" for n in range(21)]},
        {"role": "viewer", "service": WOLFBET, "keys": ["Lakai"]},
    ],
)
def test_key_scopes_that_cannot_go_together_are_refused(
    signed_in_as: Any, vault: dict[str, list[int]], scope: dict[str, Any]
) -> None:
    admin = signed_in_as(Role.ADMIN)
    assert _send(admin, "POST", "/auth/invites", {"email": new_email(), **scope}).status_code == 422


def test_an_invite_onto_a_key_grants_it_unless_the_key_is_gone(
    control_settings: Settings, signed_in_as: Any, vault: dict[str, list[int]]
) -> None:
    admin = signed_in_as(Role.ADMIN)
    email = new_email()
    body = {"email": email, "role": "viewer", "service": WOLFBET, "keys": ["lakai"]}
    assert _send(admin, "POST", "/auth/invites", body).status_code == 204
    roles = _accept(control_settings, email)
    assert [(r["lane"], r["key_ref"]) for r in roles] == [(8, "lakai"), (9, "lakai")]

    gone = new_email()
    assert _send(admin, "POST", "/auth/invites", {**body, "email": gone}).status_code == 204
    del vault["lakai"]
    response = _accept(control_settings, gone)
    assert (response.status_code, response.json()["detail"]) == (400, INVITE_INVALID)


def test_deleting_a_key_drops_its_roles_and_invites(
    control_settings: Settings, signed_in_as: Any, vault: dict[str, list[int]]
) -> None:
    admin = signed_in_as(Role.ADMIN)
    holder = _key_user(control_settings, WOLFBET, "lakai")
    email = new_email()
    body = {"email": email, "role": "viewer", "service": WOLFBET, "keys": ["lakai"]}
    assert _send(admin, "POST", "/auth/invites", body).status_code == 204
    token = admin.get("/auth/me").json()["csrf_token"]
    assert admin.delete("/wolfbet/keys/lakai", headers={"x-csrf-token": token}).status_code == 204

    async def left(session: AsyncSession) -> tuple[int, int]:
        roles = await session.scalar(
            select(func.count()).select_from(UserRole).where(UserRole.user_id == holder.id)
        )
        invites = await session.scalar(
            select(func.count()).select_from(Invite).where(Invite.email == email)
        )
        return roles or 0, invites or 0

    assert run_in_session(control_settings, left) == (0, 0)
