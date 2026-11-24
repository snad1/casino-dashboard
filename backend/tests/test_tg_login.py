"""The bot's `/web` link signs a Telegram user in once, within five minutes, as that user only."""

import base64
import hashlib
import hmac
import json
import time
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import AuditLog
from app.auth.models import Role, User, UserRole
from app.auth.telegram import LINK_INVALID, read_token
from app.settings import Settings
from tests.conftest import make_client, run_in_session
from tests.tg_links import GOLDEN, SECRET, TgUsers, golden, new_uid, sign, token

pytest_plugins = ["tests.fake_agent", "tests.control_rig", "tests.tg_rig"]


def test_golden_fixture_checksum_and_the_bots_signing_rule() -> None:
    digest, name = (GOLDEN / "SHA256SUMS").read_text().split()
    assert hashlib.sha256((GOLDEN / name).read_bytes()).hexdigest() == digest
    case = golden()
    assert sign(case["secret"], case["uid"], case["id"], case["exp"]) == case["token"]
    found = read_token(case["secret"].encode(), case["token"], case["now"])
    assert found == (case["uid"], case["id"])


@pytest.mark.parametrize(
    ("change", "now_shift"),
    [
        ({"secret": "another-secret-0123456789abcdefghij-XYZ"}, 0),
        ({}, 331),
        ({}, -331),
        ({"exp_shift": 3600}, 0),
    ],
    ids=["wrong-secret", "expired", "not-yet-issued", "too-long-lived"],
)
def test_tokens_outside_the_rules_are_refused(change: dict[str, Any], now_shift: int) -> None:
    case = golden()
    exp = case["exp"] + change.get("exp_shift", 0)
    raw = sign(change.get("secret", case["secret"]), case["uid"], case["id"], exp)
    assert read_token(case["secret"].encode(), raw, case["now"] + now_shift) is None


@pytest.mark.parametrize(
    "raw",
    ["", "no-dot", "a.b", "!!!.???", "e30.e30", "x" * 600],
    ids=["empty", "no-dot", "junk", "bad-b64", "empty-json", "long"],
)
def test_malformed_tokens_are_refused(raw: str) -> None:
    assert read_token(SECRET.encode(), raw, time.time()) is None


def test_payload_shape_is_closed() -> None:
    def raw(payload: dict[str, Any]) -> str:
        body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        mac = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest()
        return f"{body}.{base64.urlsafe_b64encode(mac).rstrip(b'=').decode()}"

    now = int(time.time())
    good = {"v": 1, "uid": 42, "id": "a" * 22, "exp": now + 60}
    assert read_token(SECRET.encode(), raw(good), now) == (42, "a" * 22)
    for bad in (
        {**good, "v": 2},
        {**good, "uid": True},
        {**good, "uid": "42"},
        {**good, "uid": 0},
        {**good, "id": "short"},
        {**good, "id": "a" * 21 + "/"},
        {**good, "role": "admin"},
        {k: v for k, v in good.items() if k != "exp"},
    ):
        assert read_token(SECRET.encode(), raw(bad), now) is None, bad


def test_a_link_signs_in_once_and_makes_a_telegram_account(
    tg_settings: Settings, tg_users: TgUsers, migrated: Settings
) -> None:
    uid = new_uid()
    tg_users.uids.append(uid)
    link = token(uid)
    with make_client(tg_settings) as client:
        assert client.post("/auth/telegram", json={"token": link}).status_code == 204
        me = client.get("/auth/me").json()
        assert me["email"] is None and me["tg_uid"] == uid
        assert me["roles"] == [{"service": None, "role": "tg_user", "lane": None}]
        again = client.post("/auth/telegram", json={"token": link})
        assert again.status_code == 401
        assert again.json() == {"detail": LINK_INVALID}

    async def account(db: AsyncSession) -> tuple[int, list[str]]:
        user = await db.scalar(select(User).where(User.tg_uid == uid))
        assert user is not None
        roles = list(await db.scalars(select(UserRole.role).where(UserRole.user_id == user.id)))
        return user.id, roles

    first, roles = run_in_session(migrated, account)
    assert roles == [Role.TG_USER]
    with make_client(tg_settings) as client:
        assert client.post("/auth/telegram", json={"token": token(uid)}).status_code == 204
    assert run_in_session(migrated, account)[0] == first


def test_a_bad_or_expired_link_gets_one_message(tg_settings: Settings) -> None:
    uid = new_uid()
    with make_client(tg_settings) as client:
        for raw in (token(uid, secret="x9" * 20), token(uid, exp=int(time.time()) - 400), "a.b"):
            reply = client.post("/auth/telegram", json={"token": raw})
            assert reply.status_code == 401
            assert reply.json() == {"detail": LINK_INVALID}
        assert client.get("/auth/me").status_code == 401


def test_a_disabled_telegram_account_cannot_sign_in(
    tg_settings: Settings, tg_users: TgUsers, migrated: Settings
) -> None:
    uid = new_uid()
    tg_users.client(uid)

    async def disable(db: AsyncSession) -> None:
        await db.execute(update(User).where(User.tg_uid == uid).values(disabled=True))
        await db.commit()

    run_in_session(migrated, disable)
    with make_client(tg_settings) as client:
        assert client.post("/auth/telegram", json={"token": token(uid)}).status_code == 401


def test_without_a_secret_the_route_is_not_there(app_settings: Settings) -> None:
    with make_client(app_settings) as client:
        assert client.post("/auth/telegram", json={"token": "a.b"}).status_code == 404


def test_a_foreign_page_cannot_sign_the_browser_in(tg_settings: Settings) -> None:
    with make_client(tg_settings) as client:
        reply = client.post(
            "/auth/telegram",
            json={"token": token(new_uid())},
            headers={"origin": "https://evil.example"},
        )
        assert reply.status_code == 403


def test_the_sign_in_is_audited_without_the_token(
    tg_settings: Settings, tg_users: TgUsers, migrated: Settings
) -> None:
    uid = new_uid()
    tg_users.client(uid)

    async def rows(db: AsyncSession) -> list[Any]:
        user_id = await db.scalar(select(User.id).where(User.tg_uid == uid))
        query = select(AuditLog).where(
            AuditLog.actor_id == user_id, AuditLog.action == "POST /auth/telegram"
        )
        return list(await db.scalars(query))

    logged = run_in_session(migrated, rows)
    assert len(logged) == 1
    assert logged[0].result == "204"
    assert logged[0].args.get("token") == "[redacted]"
