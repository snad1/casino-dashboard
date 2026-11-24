"""A lane manager runs only their own lane, with only their own keys, nothing service-wide."""

import secrets
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.lane_access import NOT_YOUR_KEY, OVER_QUOTA, added_lane
from app.auth.models import Role, UserRole
from app.auth.permissions import FORBIDDEN
from app.db.models import Service
from app.settings import Settings
from tests.conftest import Account, run_in_session
from tests.fake_agent import FakeAgent, ok

SECRET = "wb_live_FRIEND_0123456789abcdef7777"  # noqa: S105  # a fake key for tests


@pytest.fixture
def wolfbet(control_settings: Settings, account: Account) -> Any:
    """The wolfbet service row, and a way to read or give the account's lane roles."""

    async def add(session: AsyncSession) -> None:
        await session.execute(
            text(
                "INSERT INTO services (name, adapter, host) VALUES ('wolfbet', 'wolfbet', 'h') "
                "ON CONFLICT (name) DO NOTHING"
            )
        )
        await session.commit()

    run_in_session(control_settings, add)

    class Lanes:
        @staticmethod
        def owned() -> list[int]:
            async def run(session: AsyncSession) -> list[int]:
                rows = await session.scalars(
                    select(UserRole.lane).where(
                        UserRole.user_id == account.id, UserRole.lane.is_not(None)
                    )
                )
                return sorted(lane for lane in rows if lane is not None)

            return run_in_session(control_settings, run)

        @staticmethod
        def give(lane: int) -> None:
            async def run(session: AsyncSession) -> None:
                service_id = await session.scalar(
                    select(Service.id).where(Service.name == "wolfbet")
                )
                session.add(
                    UserRole(
                        user_id=account.id, service_id=service_id, role=Role.OPERATOR, lane=lane
                    )
                )
                await session.commit()

            run_in_session(control_settings, run)

    yield Lanes

    async def clean(session: AsyncSession) -> None:
        await session.execute(
            delete(UserRole).where(UserRole.user_id == account.id, UserRole.lane.is_not(None))
        )
        await session.commit()

    run_in_session(control_settings, clean)


def _post(client: TestClient, action: str, body: dict[str, Any]) -> Any:
    token = client.get("/auth/me").json()["csrf_token"]
    headers = {"x-csrf-token": token, "idempotency-key": secrets.token_urlsafe(24)}
    return client.post(f"/wolfbet/control/{action}", json=body, headers=headers)


def _bot(output: str) -> Any:
    return lambda request: [ok(request, {"exit_code": 0, "stdout": output, "stderr": ""})]


def test_a_lane_manager_creates_one_lane_and_owns_it(
    signed_in_as: Any, fake_agent: FakeAgent, wolfbet: Any
) -> None:
    client = signed_in_as(Role.LANE_MANAGER)
    fake_agent.reply = _bot("Lane 7 (friend-A) added.\n  lane 7 started\nOther lanes keep betting.")
    response = _post(client, "lane_add", {"name": "friend-A"})
    assert response.status_code == 200 and response.json()["status"] == "succeeded"
    assert wolfbet.owned() == [7]
    second = _post(client, "lane_add", {"name": "friend-B"})
    assert (second.status_code, second.json()["detail"]) == (403, OVER_QUOTA)
    assert [c["cmd"] for c in fake_agent.calls] == ["wolfbet.lane_add"]


def test_a_lane_manager_cannot_copy_someone_elses_lane(
    signed_in_as: Any, fake_agent: FakeAgent, wolfbet: Any
) -> None:
    client = signed_in_as(Role.LANE_MANAGER)
    response = _post(client, "lane_add", {"name": "friend-A", "copy_from": 2})
    assert (response.status_code, response.json()["detail"]) == (403, FORBIDDEN)
    assert fake_agent.calls == []


@pytest.mark.parametrize(
    "action,body",
    [
        ("lane_restart", {"lane": 7}),
        ("lane_stop", {"lane": 7}),
        ("pause", {"target": 7}),
        ("lane_rename", {"lane": 7, "name": "night"}),
    ],
)
def test_actions_on_their_own_lane_run(
    signed_in_as: Any, fake_agent: FakeAgent, wolfbet: Any, action: str, body: dict[str, Any]
) -> None:
    client = signed_in_as(Role.LANE_MANAGER)
    wolfbet.give(7)
    assert _post(client, action, body).status_code == 200
    assert [c["cmd"] for c in fake_agent.calls] == [f"wolfbet.{action}"]


@pytest.mark.parametrize(
    "action,body",
    [
        ("lane_restart", {"lane": 1}),
        ("lane_stop", {"lane": 3}),
        ("pause", {"target": "all"}),
        ("lanes", {"count": 2}),
        ("restart", {}),
        ("stop", {}),
        ("update", {}),
        ("config_set", {"changes": [{"key": "max_wins", "value": "3", "lane": None}]}),
        ("config_set", {"changes": [{"key": "max_wins", "value": "3", "lane": 2}]}),
    ],
)
def test_everything_outside_their_lane_is_refused(
    signed_in_as: Any, fake_agent: FakeAgent, wolfbet: Any, action: str, body: dict[str, Any]
) -> None:
    client = signed_in_as(Role.LANE_MANAGER)
    wolfbet.give(7)
    assert _post(client, action, body).status_code == 403
    assert fake_agent.calls == []


def test_config_on_their_lane_runs_with_their_own_key_only(
    signed_in_as: Any, fake_agent: FakeAgent, wolfbet: Any, account: Account
) -> None:
    client = signed_in_as(Role.LANE_MANAGER)
    wolfbet.give(7)
    keys = [
        {"id": "mine", "kind": "key", "owner_user_id": account.id},
        {"id": "theirs", "kind": "key", "owner_user_id": account.id + 999},
    ]

    def reply(request: dict[str, Any]) -> list[dict[str, Any]]:
        if request["cmd"] == "wolfbet.key_list":
            return [ok(request, {"exit_code": 0, "stderr": "", "json": {"v": 1, "keys": keys}})]
        return [ok(request, {"exit_code": 0, "stdout": "", "stderr": ""})]

    fake_agent.reply = reply
    theirs = {"changes": [{"key": "key_ref", "value": "theirs", "lane": 7}]}
    response = _post(client, "config_set", theirs)
    assert (response.status_code, response.json()["detail"]) == (403, NOT_YOUR_KEY)
    mine = {
        "changes": [
            {"key": "key_ref", "value": "mine", "lane": 7},
            {"key": "max_wins", "value": "3", "lane": 7},
        ]
    }
    assert _post(client, "config_set", mine).status_code == 200
    assert [c["cmd"] for c in fake_agent.calls] == [
        "wolfbet.key_list",
        "wolfbet.key_list",
        "wolfbet.config_set",
    ]


def test_removing_their_lane_frees_the_quota(
    signed_in_as: Any, fake_agent: FakeAgent, wolfbet: Any
) -> None:
    client = signed_in_as(Role.LANE_MANAGER)
    wolfbet.give(7)
    fake_agent.reply = _bot("Lane 7 stopped. Other lanes keep betting.")
    assert _post(client, "lane_remove", {"lane": 7}).status_code == 200
    assert wolfbet.owned() == []
    fake_agent.reply = _bot("Lane 8 (again) added.")
    assert _post(client, "lane_add", {"name": "again"}).status_code == 200
    assert wolfbet.owned() == [8]


def test_their_keys_are_the_only_keys_they_see_or_touch(
    signed_in_as: Any, fake_agent: FakeAgent, wolfbet: Any, account: Account
) -> None:
    client = signed_in_as(Role.LANE_MANAGER)
    rows = [
        {
            "id": key_id,
            "label": key_id,
            "kind": "key",
            "last4": "0000",
            "owner_user_id": owner,
            "created_at": "2026-11-24T10:00:00+00:00",
            "rotated_at": None,
            "used_by_lanes": [],
        }
        for key_id, owner in (("default", None), ("mine", account.id))
    ]
    fake_agent.reply = lambda request: [
        ok(request, {"exit_code": 0, "stderr": "", "json": {"v": 1, "keys": rows}})
    ]
    listed = client.get("/wolfbet/keys").json()["keys"]
    assert [k["id"] for k in listed] == ["mine"]
    token = client.get("/auth/me").json()["csrf_token"]
    gone = client.delete("/wolfbet/keys/default", headers={"x-csrf-token": token})
    assert (gone.status_code, gone.json()["detail"]) == (404, "No key named default.")
    assert [c["cmd"] for c in fake_agent.calls] == ["wolfbet.key_list", "wolfbet.key_list"]


def test_nothing_service_wide_opens(signed_in_as: Any, fake_agent: FakeAgent, wolfbet: Any) -> None:
    client = signed_in_as(Role.LANE_MANAGER)
    wolfbet.give(7)
    assert client.get("/wolfbet/sessions").status_code == 403
    assert client.get("/wolfbet/config").status_code == 403
    assert fake_agent.calls == []


@pytest.mark.parametrize(
    "output,lane",
    [("Lane 12 (friend-A) added with lane 2's overrides.", 12), ("garbled", None), ("", None)],
)
def test_the_new_lane_is_read_from_the_bot_output(output: str, lane: int | None) -> None:
    assert added_lane(output) == lane
