"""With goated served, each control route runs goated's own action: its arguments reach the
agent, a body only WolfBet takes is refused before the agent, and an action goated has not got
is not found; WolfBet's routes keep their bodies. goated's keys go to goated's vault."""

import secrets
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import registry
from app.auth.models import Role
from app.settings import Settings
from tests.conftest import Account, make_client
from tests.control_rig import set_role
from tests.fake_agent import JOB_ID, FakeAgent, ok
from tests.test_totp import _enrol


@pytest.fixture
def admin(control_settings: Settings, account: Account) -> Iterator[TestClient]:
    settings = control_settings.model_copy(update={"dashboard_services": ["wolfbet", "goated"]})
    set_role(settings, account.id, Role.ADMIN)
    with make_client(settings) as client:
        _enrol(client, account.email)
        yield client


def _post(client: TestClient, service: str, action: str, body: dict[str, Any]) -> Any:
    token = client.get("/auth/me").json()["csrf_token"]
    headers = {"x-csrf-token": token, "idempotency-key": secrets.token_urlsafe(24)}
    return client.post(f"/{service}/control/{action}", json=body, headers=headers)


def _jobs(fake_agent: FakeAgent) -> list[dict[str, Any]]:
    return [call["args"] for call in fake_agent.calls if call["cmd"] == "job.start"]


def test_goateds_own_bodies_reach_the_agent(admin: TestClient, fake_agent: FakeAgent) -> None:
    assert _post(admin, "goated", "benchmark", {"count": 5}).json()["job_id"] == JOB_ID
    assert _post(admin, "goated", "update", {}).status_code == 202
    assert _post(admin, "goated", "backtest", {"simulate": 100, "seed": 1}).status_code == 202
    assert _jobs(fake_agent) == [
        {"cmd": "goated.benchmark", "args": {"count": 5}},
        {"cmd": "goated.update", "args": {}},
        {
            "cmd": "goated.backtest",
            "args": {"simulate": 100, "seed": 1, "replay_session": None, "preset": None},
        },
    ]


@pytest.mark.parametrize(
    ("action", "body"),
    [
        ("benchmark", {"count": 5, "lanes": 2}),
        ("update", {"branch": "main"}),
        ("backtest", {"sessions": 10}),
        ("export", {"format": "csv"}),
    ],
)
def test_a_body_only_wolfbet_takes_is_refused_before_the_agent(
    admin: TestClient, fake_agent: FakeAgent, action: str, body: dict[str, Any]
) -> None:
    assert _post(admin, "goated", action, body).status_code == 422
    assert fake_agent.calls == []


def test_wolfbet_keeps_its_own_bodies(admin: TestClient, fake_agent: FakeAgent) -> None:
    assert _post(admin, "wolfbet", "benchmark", {"count": 5, "lanes": 2}).status_code == 202
    assert _post(admin, "wolfbet", "update", {"branch": "main"}).status_code == 202
    assert _jobs(fake_agent) == [
        {"cmd": "wolfbet.benchmark", "args": {"count": 5, "lanes": 2}},
        {"cmd": "wolfbet.update", "args": {"branch": "main"}},
    ]


def test_an_action_goated_has_not_got_is_not_found(
    admin: TestClient, fake_agent: FakeAgent
) -> None:
    assert _post(admin, "goated", "pause", {"target": "all"}).status_code == 404
    assert fake_agent.calls == []


def test_a_shared_action_runs_goateds_command(admin: TestClient, fake_agent: FakeAgent) -> None:
    assert _post(admin, "goated", "version", {}).status_code == 200
    assert _post(admin, "goated", "lane_stop", {"lane": 2}).status_code == 200
    assert [(c["cmd"], c["args"]) for c in fake_agent.calls] == [
        ("goated.version", {}),
        ("goated.lane_stop", {"lane": 2}),
    ]


def test_goateds_menu_lists_its_own_buttons(admin: TestClient) -> None:
    menu = admin.get("/goated/control").json()["actions"]
    names = {action["name"] for action in menu}
    assert {"pause", "resume", "reload"}.isdisjoint(names) and "benchmark" in names
    benchmark = next(a for a in menu if a["name"] == "benchmark")
    assert "0-amount bet" in benchmark["effect"] and benchmark["confirm_first"] is True


SECRET = "gt_live_FRIEND_0123456789abcdef7777"  # noqa: S105  # a fake key for tests
ROW = {
    "id": "friend-a",
    "label": "Friend A",
    "kind": "key",
    "last4": "7777",
    "owner_user_id": None,
    "created_at": "2026-11-27T10:00:00+00:00",
    "rotated_at": None,
    "used_by_lanes": [2],
}


def test_goateds_keys_go_to_goateds_vault(
    admin: TestClient, fake_agent: FakeAgent, account: Account
) -> None:
    token = {"x-csrf-token": admin.get("/auth/me").json()["csrf_token"]}
    fake_agent.reply = lambda request: [
        ok(request, {"exit_code": 0, "stderr": "", "json": {"v": 1, "keys": [ROW]}})
    ]
    listed = admin.get("/goated/keys")
    assert listed.status_code == 200 and listed.json()["keys"][0]["used_by_lanes"] == [2]
    fake_agent.reply = lambda request: [
        ok(request, {"exit_code": 0, "stderr": "", "json": {"v": 1, "key": ROW, "note": None}})
    ]
    added = admin.post("/goated/keys", json={"label": "Friend A", "secret": SECRET}, headers=token)
    assert added.status_code == 201 and SECRET not in added.text
    assert [c["cmd"] for c in fake_agent.calls] == ["goated.key_list", "goated.key_put"]
    assert fake_agent.calls[-1]["args"]["owner_user_id"] == account.id


def test_the_menu_names_the_fields_each_services_action_takes(admin: TestClient) -> None:
    menus = {
        svc: {a["name"]: a["fields"] for a in admin.get(f"/{svc}/control").json()["actions"]}
        for svc in ("wolfbet", "goated")
    }
    assert menus["wolfbet"]["update"] == {"branch": False}
    assert menus["goated"]["update"] == {}
    assert menus["wolfbet"]["export"] == {"session_id": False, "format": False}
    assert menus["goated"]["export"] == {"session_id": True, "format": False}
    assert menus["goated"]["benchmark"] == {"count": False}
    for svc, menu in menus.items():
        for name, fields in menu.items():
            body = registry.KNOWN[svc].controls[name].body
            assert fields == {f: i.is_required() for f, i in body.model_fields.items()}, name
