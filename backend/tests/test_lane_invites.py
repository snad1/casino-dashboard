"""Invites onto chosen lanes or as a lane manager, only onto lanes the bot has, and lane quotas."""

import re
import secrets
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.control import AGENT_DOWN
from app.api.lane_access import over_quota
from app.auth.admin import LANE_UNKNOWN, OWNER_ONLY
from app.auth.invites import INVITE_INVALID
from app.auth.models import Invite, Role, User, UserRole
from app.auth.stepup import STEP_UP_NEEDED
from app.mail.models import OutboxMessage
from app.settings import Settings
from tests.conftest import Account, make_client, make_user, new_email, run_in_session
from tests.fake_agent import FakeAgent, default_reply, fail, ok

WOLFBET = "wolfbet"
PASSWORD = "lantern-orbit-" + secrets.token_hex(6)
TOKEN = re.compile(r"#token=([A-Za-z0-9_-]+)")


@pytest.fixture(autouse=True)
def _leave_no_lane_data(control_settings: Settings) -> Iterator[None]:
    """Drop the lane invites, lane roles and raised quotas a test left, so the migration
    round trip (which refuses them) still runs after this file."""
    yield

    async def clean(session: AsyncSession) -> None:
        await session.execute(delete(Invite).where(func.cardinality(Invite.lanes) > 0))
        await session.execute(delete(Invite).where(Invite.role == "lane_manager"))
        await session.execute(delete(UserRole).where(UserRole.lane.is_not(None)))
        await session.execute(delete(UserRole).where(UserRole.role == Role.LANE_MANAGER))
        await session.execute(update(User).where(User.lane_quota != 1).values(lane_quota=1))
        await session.commit()

    run_in_session(control_settings, clean)


@pytest.fixture
def lanes(control_settings: Settings, fake_agent: FakeAgent) -> Iterator[list[int]]:
    """The bot's lanes, as the agent reports them; edit the list to add or remove one."""

    async def add(session: AsyncSession) -> None:
        await session.execute(
            text(
                "INSERT INTO services (name, adapter, host) VALUES ('wolfbet', 'wolfbet', 'h')"
                " ON CONFLICT (name) DO NOTHING"
            )
        )
        await session.commit()

    run_in_session(control_settings, add)
    registry = [1, 3, 4]

    def reply(request: dict[str, Any]) -> list[dict[str, Any]]:
        if request["cmd"] == "wolfbet.lane_ids":
            stdout = " ".join(map(str, registry)) + "\n"
            return [ok(request, {"exit_code": 0, "stdout": stdout, "stderr": ""})]
        if request["cmd"] == "wolfbet.lane_add":
            lane = max(registry) + 1
            registry.append(lane)
            stdout = f"Lane {lane} (friend) added.\n"
            return [ok(request, {"exit_code": 0, "stdout": stdout, "stderr": ""})]
        return default_reply(request)

    fake_agent.reply = reply
    yield registry


def _send(client: TestClient, method: str, path: str, body: dict[str, Any]) -> Any:
    token = client.get("/auth/me").json()["csrf_token"]
    headers = {"x-csrf-token": token, "idempotency-key": secrets.token_urlsafe(24)}
    return client.request(method, path, json=body, headers=headers)


def _token_for(settings: Settings, email: str) -> str:
    async def run(session: AsyncSession) -> str:
        query = select(OutboxMessage.text_body).where(OutboxMessage.to_email == email)
        body = await session.scalar(query.order_by(OutboxMessage.id.desc()).limit(1))
        match = TOKEN.search(body or "")
        assert match, body
        return match.group(1)

    return run_in_session(settings, run)


def _invites(settings: Settings, email: str) -> list[tuple[str, list[int]]]:
    async def run(session: AsyncSession) -> list[tuple[str, list[int]]]:
        rows = await session.execute(select(Invite.role, Invite.lanes).where(Invite.email == email))
        return [(role, list(lanes)) for role, lanes in rows]

    return run_in_session(settings, run)


def _accept(settings: Settings, email: str) -> Any:
    with make_client(settings) as guest:
        body = {"token": _token_for(settings, email), "password": PASSWORD}
        response = guest.post("/auth/invites/accept", json=body)
        if response.status_code != 204:
            return response
        assert (
            guest.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code
            == 204
        )
        return guest.get("/auth/me").json()["roles"]


def test_an_invite_onto_a_lane_grants_that_lane_only(
    control_settings: Settings, signed_in_as: Any, lanes: list[int]
) -> None:
    admin = signed_in_as(Role.ADMIN)
    email = new_email()
    body = {"email": email, "role": "viewer", "service": WOLFBET, "lanes": [3]}
    assert _send(admin, "POST", "/auth/invites", body).status_code == 204
    assert _invites(control_settings, email) == [("viewer", [3])]
    mail = run_in_session(
        control_settings,
        lambda s: s.scalar(select(OutboxMessage.text_body).where(OutboxMessage.to_email == email)),
    )
    assert "as viewer on wolfbet lane 3" in (mail or "")
    assert _accept(control_settings, email) == [
        {"service": WOLFBET, "role": "viewer", "lane": 3, "key_ref": None}
    ]


def test_an_invite_onto_several_lanes_grants_each_of_them(
    control_settings: Settings, signed_in_as: Any, lanes: list[int]
) -> None:
    admin = signed_in_as(Role.ADMIN)
    email = new_email()
    body = {"email": email, "role": "viewer", "service": WOLFBET, "lanes": [4, 3]}
    assert _send(admin, "POST", "/auth/invites", body).status_code == 204
    assert _invites(control_settings, email) == [("viewer", [3, 4])]
    mail = run_in_session(
        control_settings,
        lambda s: s.scalar(select(OutboxMessage.text_body).where(OutboxMessage.to_email == email)),
    )
    assert "as viewer on wolfbet lanes 3, 4" in (mail or "")
    roles = _accept(control_settings, email)
    assert sorted(roles, key=lambda r: r["lane"]) == [
        {"service": WOLFBET, "role": "viewer", "lane": 3, "key_ref": None},
        {"service": WOLFBET, "role": "viewer", "lane": 4, "key_ref": None},
    ]


def test_one_unknown_lane_among_several_refuses_the_invite(
    control_settings: Settings, signed_in_as: Any, lanes: list[int]
) -> None:
    admin = signed_in_as(Role.ADMIN)
    email = new_email()
    body = {"email": email, "role": "viewer", "service": WOLFBET, "lanes": [3, 42]}
    response = _send(admin, "POST", "/auth/invites", body)
    assert (response.status_code, response.json()["detail"]) == (422, LANE_UNKNOWN)
    assert _invites(control_settings, email) == []


def test_a_lane_manager_invite_lands_on_the_service(
    control_settings: Settings, signed_in_as: Any, lanes: list[int]
) -> None:
    admin = signed_in_as(Role.ADMIN)
    email = new_email()
    body = {"email": email, "role": "lane_manager", "service": WOLFBET}
    assert _send(admin, "POST", "/auth/invites", body).status_code == 204
    assert _accept(control_settings, email) == [
        {"service": WOLFBET, "role": "lane_manager", "lane": None, "key_ref": None}
    ]


def test_an_unknown_lane_is_refused_and_nothing_is_sent(
    control_settings: Settings, signed_in_as: Any, lanes: list[int]
) -> None:
    admin = signed_in_as(Role.ADMIN)
    email = new_email()
    body = {"email": email, "role": "viewer", "service": WOLFBET, "lanes": [42]}
    response = _send(admin, "POST", "/auth/invites", body)
    assert (response.status_code, response.json()["detail"]) == (422, LANE_UNKNOWN)
    assert _invites(control_settings, email) == []


def test_a_lane_removed_before_acceptance_voids_the_invite(
    control_settings: Settings, signed_in_as: Any, lanes: list[int]
) -> None:
    admin = signed_in_as(Role.ADMIN)
    email = new_email()
    body = {"email": email, "role": "operator", "service": WOLFBET, "lanes": [3]}
    assert _send(admin, "POST", "/auth/invites", body).status_code == 204
    lanes.remove(3)
    response = _accept(control_settings, email)
    assert (response.status_code, response.json()["detail"]) == (400, INVITE_INVALID)
    found = run_in_session(
        control_settings, lambda s: s.scalar(select(User.id).where(User.email == email))
    )
    assert found is None


def test_the_bot_host_down_grants_nothing(
    control_settings: Settings, signed_in_as: Any, lanes: list[int], fake_agent: FakeAgent
) -> None:
    admin = signed_in_as(Role.ADMIN)
    fake_agent.reply = lambda request: [fail(request, "unavailable")]
    email = new_email()
    body = {"email": email, "role": "viewer", "service": WOLFBET, "lanes": [3]}
    response = _send(admin, "POST", "/auth/invites", body)
    assert (response.status_code, response.json()["detail"]) == (502, AGENT_DOWN)
    assert _invites(control_settings, email) == []


@pytest.mark.parametrize(
    "scope",
    [
        {"role": "admin", "service": WOLFBET, "lanes": [3]},
        {"role": "viewer", "lanes": [3]},
        {"role": "lane_manager"},
        {"role": "viewer", "service": WOLFBET, "lanes": [0]},
        {"role": "viewer", "service": WOLFBET, "lanes": [3, 3]},
        {"role": "viewer", "service": WOLFBET, "lanes": list(range(1, 22))},
    ],
)
def test_scopes_that_cannot_go_together_are_refused(
    signed_in_as: Any, lanes: list[int], scope: dict[str, Any]
) -> None:
    admin = signed_in_as(Role.ADMIN)
    assert _send(admin, "POST", "/auth/invites", {"email": new_email(), **scope}).status_code == 422


def test_the_users_page_checks_the_lane_exists(
    control_settings: Settings, signed_in_as: Any, lanes: list[int], fake_agent: FakeAgent
) -> None:
    admin = signed_in_as(Role.ADMIN)
    target = make_user(control_settings, Role.VIEWER, two_factor=False)
    path = f"/admin/users/{target.id}/role"
    unknown = _send(admin, "PUT", path, {"role": "viewer", "service": WOLFBET, "lanes": [42]})
    assert (unknown.status_code, unknown.json()["detail"]) == (422, LANE_UNKNOWN)
    assert (
        _send(admin, "PUT", path, {"role": "viewer", "service": WOLFBET, "lanes": [3]}).status_code
        == 204
    )
    assert fake_agent.cmds() == ["wolfbet.lane_ids", "wolfbet.lane_ids"]


def test_the_users_page_replaces_a_users_lanes(
    control_settings: Settings, signed_in_as: Any, lanes: list[int], fake_agent: FakeAgent
) -> None:
    admin = signed_in_as(Role.ADMIN)
    target = make_user(control_settings, Role.VIEWER, two_factor=False)
    path = f"/admin/users/{target.id}/role"
    first = {"role": "viewer", "service": WOLFBET, "lanes": [1, 3]}
    assert _send(admin, "PUT", path, first).status_code == 204
    second = {"role": "operator", "service": WOLFBET, "lanes": [3, 4]}
    assert _send(admin, "PUT", path, second).status_code == 204
    listed = {u["id"]: u for u in admin.get("/admin/users").json()["items"]}
    assert sorted(listed[target.id]["roles"], key=lambda r: r["lane"]) == [
        {"service": WOLFBET, "role": "operator", "lane": 3, "key_ref": None},
        {"service": WOLFBET, "role": "operator", "lane": 4, "key_ref": None},
    ]
    assert fake_agent.cmds() == ["wolfbet.lane_ids", "wolfbet.lane_ids"]


def _quota(settings: Settings, user_id: int) -> int:
    found = run_in_session(
        settings, lambda s: s.scalar(select(User.lane_quota).where(User.id == user_id))
    )
    assert found is not None
    return int(found)


def test_only_the_owner_sets_a_lane_quota(
    control_settings: Settings, signed_in_as: Any, client_as: Any, lanes: list[int]
) -> None:
    target = make_user(control_settings, Role.VIEWER, two_factor=False)
    path = f"/admin/users/{target.id}/lane-quota"
    admin = client_as(make_user(control_settings, Role.ADMIN, two_factor=True))
    refused = admin.put(path, json={"lane_quota": 3})
    assert (refused.status_code, refused.json()["detail"]) == (403, OWNER_ONLY)
    owner = signed_in_as(Role.OWNER)
    assert _send(owner, "PUT", path, {"lane_quota": 3}).status_code == 204
    assert _quota(control_settings, target.id) == 3
    listed = {u["id"]: u for u in owner.get("/admin/users").json()["items"]}
    assert listed[target.id]["lane_quota"] == 3
    for bad in (0, 100):
        assert _send(owner, "PUT", path, {"lane_quota": bad}).status_code == 422


def test_a_quota_change_needs_a_fresh_second_factor(
    control_settings: Settings, signed_in_as: Any, lanes: list[int]
) -> None:
    target = make_user(control_settings, Role.VIEWER, two_factor=False)
    owner = signed_in_as(Role.OWNER, two_factor=False)
    response = _send(owner, "PUT", f"/admin/users/{target.id}/lane-quota", {"lane_quota": 2})
    assert response.status_code == 403
    assert response.json()["detail"] in (
        STEP_UP_NEEDED,
        "Turn on two-factor authentication to use this.",
    )
    assert _quota(control_settings, target.id) == 1


def test_a_lane_manager_creates_lanes_up_to_their_quota(
    control_settings: Settings, signed_in_as: Any, lanes: list[int], account: Account
) -> None:
    client = signed_in_as(Role.LANE_MANAGER)

    async def raise_quota(session: AsyncSession) -> None:
        await session.execute(update(User).where(User.id == account.id).values(lane_quota=2))
        await session.commit()

    async def clean(session: AsyncSession) -> None:
        await session.execute(
            delete(UserRole).where(UserRole.user_id == account.id, UserRole.lane.is_not(None))
        )
        await session.execute(update(User).where(User.id == account.id).values(lane_quota=1))
        await session.commit()

    run_in_session(control_settings, raise_quota)
    try:
        for name in ("friend-a", "friend-b"):
            added = _send(client, "POST", "/wolfbet/control/lane_add", {"name": name})
            assert added.status_code == 200, added.json()
        third = _send(client, "POST", "/wolfbet/control/lane_add", {"name": "friend-c"})
        assert (third.status_code, third.json()["detail"]) == (403, over_quota(2))
        assert over_quota(2) == "You already run 2 lanes, the most you may have."
    finally:
        run_in_session(control_settings, clean)
