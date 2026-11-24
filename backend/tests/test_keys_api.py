"""Vault keys from the dashboard: admin only, stepped up, and the key never comes back out."""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.control import AGENT_DOWN
from app.api.keys import BAD_LABEL, VAULT_DOWN, key_id_for
from app.audit import AuditLog
from app.auth.models import ApiSession, Role
from app.auth.stepup import STEP_UP_NEEDED
from app.settings import Settings
from tests.conftest import Account, run_in_session
from tests.fake_agent import FakeAgent, fail, ok

SECRET = "wb_live_FRIEND_0123456789abcdef7777"  # noqa: S105  # a fake key for tests


def _row(key_id: str = "friend-a", **extra: Any) -> dict[str, Any]:
    return {
        "id": key_id,
        "label": "Friend A",
        "kind": "key",
        "last4": "7777",
        "owner_user_id": None,
        "created_at": "2026-11-23T10:00:00+00:00",
        "rotated_at": None,
        "used_by_lanes": [],
        **extra,
    }


def _bot(doc: dict[str, Any], exit_code: int = 0) -> Any:
    return lambda request: [ok(request, {"exit_code": exit_code, "stderr": "", "json": doc})]


def _csrf(client: TestClient) -> dict[str, str]:
    return {"x-csrf-token": client.get("/auth/me").json()["csrf_token"]}


def _add(client: TestClient, body: dict[str, Any] | None = None) -> Any:
    payload = {"label": "Friend A", "secret": SECRET} if body is None else body
    return client.post("/wolfbet/keys", json=payload, headers=_csrf(client))


def test_an_admin_adds_a_key_that_never_comes_back(
    signed_in_as: Any, fake_agent: FakeAgent, account: Account
) -> None:
    client = signed_in_as(Role.ADMIN)
    fake_agent.reply = _bot({"v": 1, "key": _row(owner_user_id=account.id), "note": None})
    response = _add(client)
    assert response.status_code == 201
    assert SECRET not in response.text
    assert response.json()["key"]["id"] == "friend-a"
    call = fake_agent.calls[-1]
    assert call["cmd"] == "wolfbet.key_put"
    assert call["args"] == {
        "key_id": "friend-a",
        "label": "Friend A",
        "secret": SECRET,
        "owner_user_id": account.id,
        "kind": "key",
    }


def test_the_owner_comes_from_the_session_not_the_body(
    signed_in_as: Any, fake_agent: FakeAgent
) -> None:
    client = signed_in_as(Role.ADMIN)
    response = _add(client, {"label": "x1", "secret": SECRET, "owner_user_id": 1})
    assert response.status_code == 422
    assert fake_agent.calls == []


def test_the_audit_row_never_holds_the_key(
    signed_in_as: Any, fake_agent: FakeAgent, control_settings: Settings, account: Account
) -> None:
    client = signed_in_as(Role.ADMIN)
    fake_agent.reply = _bot({"v": 1, "key": _row(), "note": None})
    _add(client)
    client.post(
        "/wolfbet/keys/friend-a/rotate", json={"secret": SECRET + "x"}, headers=_csrf(client)
    )

    async def run(session: AsyncSession) -> list[str]:
        rows = await session.scalars(select(AuditLog).where(AuditLog.actor_email == account.email))
        return [
            json.dumps({c.name: str(getattr(r, c.name)) for c in AuditLog.__table__.columns})
            for r in rows
        ]

    rows = run_in_session(control_settings, run)
    assert any("/keys" in row for row in rows)
    assert all(SECRET not in row for row in rows)


@pytest.mark.parametrize("role", [Role.OPERATOR, Role.VIEWER])
def test_only_owners_and_admins_manage_keys(
    signed_in_as: Any, fake_agent: FakeAgent, role: Role
) -> None:
    client = signed_in_as(role)
    assert client.get("/wolfbet/keys").status_code == 403
    assert _add(client).status_code == 403
    assert fake_agent.calls == []


def test_writes_need_a_recent_step_up(
    signed_in_as: Any, fake_agent: FakeAgent, control_settings: Settings, account: Account
) -> None:
    client = signed_in_as(Role.ADMIN)

    async def expire(session: AsyncSession) -> None:
        query = update(ApiSession).where(ApiSession.user_id == account.id)
        await session.execute(query.values(step_up_at=None))
        await session.commit()

    run_in_session(control_settings, expire)
    fake_agent.reply = _bot({"v": 1, "keys": []})
    for response in (
        _add(client),
        client.post("/wolfbet/keys/a/rotate", json={"secret": SECRET}, headers=_csrf(client)),
        client.delete("/wolfbet/keys/a", headers=_csrf(client)),
    ):
        assert response.status_code == 403
        assert response.json()["detail"] == STEP_UP_NEEDED
    assert client.get("/wolfbet/keys").status_code == 200
    assert [c["cmd"] for c in fake_agent.calls] == ["wolfbet.key_list"]


def test_the_list_shows_lanes_and_can_keep_to_my_keys(
    signed_in_as: Any, fake_agent: FakeAgent, account: Account
) -> None:
    client = signed_in_as(Role.ADMIN)
    rows = [_row("default", used_by_lanes=[1, 2]), _row(owner_user_id=account.id)]
    fake_agent.reply = _bot({"v": 1, "keys": rows})
    keys = client.get("/wolfbet/keys").json()["keys"]
    assert [(k["id"], k["used_by_lanes"]) for k in keys] == [("default", [1, 2]), ("friend-a", [])]
    mine = client.get("/wolfbet/keys", params={"mine": True}).json()["keys"]
    assert [k["id"] for k in mine] == ["friend-a"]


@pytest.mark.parametrize(
    "code,status,message",
    [
        ("key_exists", 409, "A key named friend-a already exists."),
        ("key_in_use", 409, "This key is used by lane 1, 2. Move the lanes to another key first."),
        ("key_unknown", 404, "No key named friend-a."),
    ],
)
def test_bot_refusals_keep_their_message(
    signed_in_as: Any, fake_agent: FakeAgent, code: str, status: int, message: str
) -> None:
    client = signed_in_as(Role.ADMIN)
    fake_agent.reply = _bot({"v": 1, "error": {"code": code, "message": message}}, exit_code=2)
    response = client.delete("/wolfbet/keys/friend-a", headers=_csrf(client))
    assert (response.status_code, response.json()["detail"]) == (status, message)


@pytest.mark.parametrize(
    "body",
    [
        {"label": "x", "secret": "short"},
        {"label": "x", "secret": "has a space inside"},
        {"label": " x", "secret": SECRET},
        {"label": "a\nb", "secret": SECRET},
        {"label": "x"},
        {"secret": SECRET},
    ],
)
def test_bad_bodies_reach_no_agent(signed_in_as: Any, fake_agent: FakeAgent, body: Any) -> None:
    client = signed_in_as(Role.ADMIN)
    assert _add(client, body).status_code == 422
    assert fake_agent.calls == []


def test_a_label_without_letters_or_digits_is_refused(
    signed_in_as: Any, fake_agent: FakeAgent
) -> None:
    client = signed_in_as(Role.ADMIN)
    response = _add(client, {"label": "!!!", "secret": SECRET})
    assert (response.status_code, response.json()["detail"]) == (422, BAD_LABEL)
    assert fake_agent.calls == []


def test_bad_key_ids_in_the_path_reach_no_agent(signed_in_as: Any, fake_agent: FakeAgent) -> None:
    client = signed_in_as(Role.ADMIN)
    assert client.delete("/wolfbet/keys/Friend", headers=_csrf(client)).status_code == 422
    assert fake_agent.calls == []


@pytest.mark.parametrize(
    "reply,detail",
    [
        (lambda request: [fail(request, "unreachable")], AGENT_DOWN),
        (_bot({"v": 1, "error": {"code": "vault_unavailable", "message": "x"}}, 1), VAULT_DOWN),
    ],
)
def test_host_problems_are_one_fixed_message(
    signed_in_as: Any, fake_agent: FakeAgent, reply: Any, detail: str
) -> None:
    client = signed_in_as(Role.ADMIN)
    fake_agent.reply = reply
    response = client.get("/wolfbet/keys")
    assert (response.status_code, response.json()["detail"]) == (502, detail)


def test_other_services_have_no_keys(signed_in_as: Any, fake_agent: FakeAgent) -> None:
    client = signed_in_as(Role.ADMIN)
    assert client.get("/goated/keys").status_code in (403, 404)
    assert fake_agent.calls == []


@pytest.mark.parametrize(
    "label,key_id",
    [("Friend A", "friend-a"), ("  night__run 2 ", "night-run-2"), ("x" * 40, "x" * 32)],
)
def test_labels_become_vault_ids(label: str, key_id: str) -> None:
    assert key_id_for(label) == key_id


def test_a_proxy_entry_is_sent_with_its_kind(signed_in_as: Any, fake_agent: FakeAgent) -> None:
    client = signed_in_as(Role.ADMIN)
    fake_agent.reply = _bot({"v": 1, "key": _row("friend-proxy", kind="proxy"), "note": None})
    body = {"label": "Friend proxy", "secret": "socks5://u:p@10.0.0.9:1080", "kind": "proxy"}
    response = _add(client, body)
    assert response.status_code == 201 and response.json()["key"]["kind"] == "proxy"
    assert fake_agent.calls[-1]["args"]["kind"] == "proxy"
    assert _add(client, {**body, "kind": "cookie"}).status_code == 422


TG_TOKEN = "123456789:AAH-telegram-bot-token-x9Yz0123456789ab"  # noqa: S105  # a fake token


def test_the_owner_stores_the_telegram_token_as_telegram(
    signed_in_as: Any, fake_agent: FakeAgent
) -> None:
    client = signed_in_as(Role.OWNER)
    note = "Restart the Telegram bot to use the new token."
    fake_agent.reply = _bot({"v": 1, "key": _row("telegram", kind="telegram"), "note": note})
    body = {"label": "Telegram bot", "secret": TG_TOKEN, "kind": "telegram"}
    response = _add(client, body)
    assert response.status_code == 201
    assert (response.json()["key"]["kind"], response.json()["note"]) == ("telegram", note)
    assert TG_TOKEN not in response.text
    args = fake_agent.calls[-1]["args"]
    assert (args["key_id"], args["kind"]) == ("telegram", "telegram")
    bad = _add(client, {**body, "secret": "not-a-telegram-token"})
    assert (bad.status_code, bad.json()["detail"]) == (
        422,
        "That doesn't look like a Telegram bot token.",
    )


def test_an_admin_cannot_touch_the_telegram_token(signed_in_as: Any, fake_agent: FakeAgent) -> None:
    client = signed_in_as(Role.ADMIN)
    refusal = (403, "Only the owner can change the Telegram bot token.")
    added = _add(client, {"label": "Telegram bot", "secret": TG_TOKEN, "kind": "telegram"})
    assert (added.status_code, added.json()["detail"]) == refusal
    rotated = client.post(
        "/wolfbet/keys/telegram/rotate", json={"secret": TG_TOKEN}, headers=_csrf(client)
    )
    assert (rotated.status_code, rotated.json()["detail"]) == refusal
    deleted = client.delete("/wolfbet/keys/telegram", headers=_csrf(client))
    assert (deleted.status_code, deleted.json()["detail"]) == refusal
    assert fake_agent.calls == []
    fake_agent.reply = _bot({"v": 1, "key": _row(), "note": None})
    assert _add(client).status_code == 201
