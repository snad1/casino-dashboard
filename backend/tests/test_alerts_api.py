"""The alerts api: who may read and change rules, test-fire, history and a user's channels."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.channels import handle_update
from app.alerts.models import AlertDelivery, AlertEvent, AlertRule, PushSubscription, UserChannels
from app.api.alerts import (
    INVALID_SETTINGS,
    NO_CHANNEL,
    NO_PUSH,
    NO_TELEGRAM,
    QUIET_BOTH,
    TOO_MANY_DEVICES,
)
from app.auth.models import Role, User, UserRole
from app.settings import Settings
from tests.conftest import NO_LOGIN, make_client, new_email, run_in_session
from tests.test_alert_push import FCM, browser_keys

RULES = "/wolfbet/alerts/rules"
DOWN: dict[str, Any] = {
    "name": "Bot down",
    "kind": "down",
    "params": {"statuses": ["down"], "for_s": 60},
    "cooldown_s": 600,
    "enabled": True,
}


def csrf(client: TestClient) -> dict[str, str]:
    token: str = client.get("/auth/me").json()["csrf_token"]
    return {"x-csrf-token": token}


class Reloads:
    service = "wolfbet"

    def __init__(self) -> None:
        self.count = 0

    async def reload(self) -> None:
        self.count += 1


@pytest.fixture
def owner(signed_in_as: Any) -> TestClient:
    client: TestClient = signed_in_as(Role.OWNER)
    return client


@pytest.fixture(autouse=True)
def clean(control_settings: Settings) -> Iterator[None]:
    """Leave only the seeded rules, and no events, for wolfbet."""
    yield

    async def run(session: AsyncSession) -> None:
        await session.execute(delete(AlertEvent).where(AlertEvent.service == "wolfbet"))
        await session.execute(
            delete(AlertRule).where(AlertRule.service == "wolfbet", AlertRule.id > 5)
        )
        await session.commit()

    run_in_session(control_settings, run)


def test_owners_create_edit_and_delete_rules_and_the_engine_reloads(owner: TestClient) -> None:
    reloads = Reloads()
    owner.app.state.alerts = reloads  # type: ignore[attr-defined]
    created = owner.post(RULES, json=DOWN, headers=csrf(owner))
    assert created.status_code == 201
    rule = created.json()
    assert rule["params"] == {"statuses": ["down"], "for_s": 60}
    assert rule["kind"] == "down" and rule["enabled"] is True

    edited = owner.put(
        f"{RULES}/{rule['id']}",
        json={**DOWN, "lane": 2, "params": {"statuses": ["down", "stale"], "for_s": 300}},
        headers=csrf(owner),
    )
    assert edited.status_code == 200
    assert (edited.json()["lane"], edited.json()["params"]["for_s"]) == (2, 300)
    assert rule["id"] in [r["id"] for r in owner.get(RULES).json()]

    assert owner.delete(f"{RULES}/{rule['id']}", headers=csrf(owner)).status_code == 204
    assert rule["id"] not in [r["id"] for r in owner.get(RULES).json()]
    assert owner.delete(f"{RULES}/{rule['id']}", headers=csrf(owner)).status_code == 404
    assert reloads.count == 3


def test_params_are_filled_in_and_checked_like_the_engine(owner: TestClient) -> None:
    filled = owner.post(RULES, json={**DOWN, "params": {}}, headers=csrf(owner))
    assert filled.json()["params"] == {"statuses": ["down"], "for_s": 60}
    for bad in (
        {"params": {"pct": 500}, "kind": "drawdown"},
        {"params": {"at": 1, "step": 1}, "kind": "profit"},
        {"params": {"pattern": "(a+)+$"}, "kind": "stop_reason"},
        {"params": {"for_s": 60, "extra": True}},
    ):
        response = owner.post(RULES, json={**DOWN, **bad}, headers=csrf(owner))
        assert response.status_code == 422
        assert response.json()["detail"] == INVALID_SETTINGS
    for invalid in (
        {"kind": "terminal_opened"},
        {"lane": 11},
        {"name": ""},
        {"name": "x" * 81},
        {"name": "bad\nname"},
        {"cooldown_s": -1},
        {"surprise": 1},
    ):
        assert owner.post(RULES, json={**DOWN, **invalid}, headers=csrf(owner)).status_code == 422


@pytest.mark.parametrize(
    ("role", "two_factor"),
    [(Role.VIEWER, False), (Role.OPERATOR, True), (Role.ADMIN, False)],
)
def test_only_owners_and_admins_with_two_factor_change_rules(
    signed_in_as: Any, control_settings: Settings, role: Role, two_factor: bool
) -> None:
    async def add(session: AsyncSession) -> int:
        row = AlertRule(service="wolfbet", kind="down", name="Bot down", params={})
        session.add(row)
        await session.commit()
        return row.id

    rule_id = run_in_session(control_settings, add)
    client = signed_in_as(role, two_factor=two_factor)
    assert client.get(RULES).status_code == 200
    assert client.get("/wolfbet/alerts/events").status_code == 200
    headers = csrf(client)
    assert client.post(RULES, json=DOWN, headers=headers).status_code == 403
    assert client.put(f"{RULES}/{rule_id}", json=DOWN, headers=headers).status_code == 403
    assert client.delete(f"{RULES}/{rule_id}", headers=headers).status_code == 403
    assert client.post(f"{RULES}/{rule_id}/test", headers=headers).status_code == 403


def test_signed_out_users_and_unknown_services_get_nothing(
    control_settings: Settings, owner: TestClient
) -> None:
    with make_client(control_settings) as anonymous:
        assert anonymous.get(RULES).status_code == 401
        assert anonymous.get("/alerts/me/channels").status_code == 401
    assert owner.get("/goated/alerts/rules").status_code == 404


def _deliveries(settings: Settings) -> list[tuple[int | None, str, str]]:
    async def run(session: AsyncSession) -> list[tuple[int | None, str, str]]:
        rows = await session.execute(
            select(AlertDelivery.user_id, AlertDelivery.channel, AlertEvent.message)
            .join(AlertEvent, AlertEvent.id == AlertDelivery.event_id)
            .where(AlertEvent.kind == "test")
        )
        return [tuple(r) for r in rows]  # type: ignore[misc]

    return run_in_session(settings, run)


def test_a_test_alert_goes_to_the_caller_only(
    owner: TestClient, control_settings: Settings
) -> None:
    rule_id = owner.post(RULES, json=DOWN, headers=csrf(owner)).json()["id"]
    refused = owner.post(f"{RULES}/{rule_id}/test", headers=csrf(owner))
    assert (refused.status_code, refused.json()["detail"]) == (422, NO_CHANNEL)

    async def someone_else_with_email_on(session: AsyncSession) -> int:
        other = User(email=new_email(), password_hash=NO_LOGIN)
        session.add(other)
        await session.flush()
        session.add(UserRole(user_id=other.id, service_id=None, role=Role.ADMIN))
        session.add(UserChannels(user_id=other.id, email_enabled=True))
        await session.commit()
        return other.id

    other_id = run_in_session(control_settings, someone_else_with_email_on)
    on = {"email_enabled": True, "telegram_enabled": False}
    assert owner.put("/alerts/me/channels", json=on, headers=csrf(owner)).status_code == 200
    fired = owner.post(f"{RULES}/{rule_id}/test", headers=csrf(owner))
    assert fired.json() == {"channels": ["email"]}

    rows = _deliveries(control_settings)
    assert len(rows) == 1
    user_id, channel, message = rows[0]
    assert (channel, message) == ("email", "Test alert: Bot down")
    assert user_id not in (None, other_id)
    history = owner.get("/wolfbet/alerts/events").json()["items"]
    assert history[0]["kind"] == "test"


def test_history_pages_newest_first(owner: TestClient, control_settings: Settings) -> None:
    async def seed(session: AsyncSession) -> None:
        session.add_all(
            AlertEvent(service="wolfbet", kind="down", message=f"event {i}") for i in range(5)
        )
        await session.commit()

    run_in_session(control_settings, seed)

    async def summary(session: AsyncSession) -> None:
        session.add(AlertEvent(service="wolfbet", kind="digest", message="Daily summary"))
        await session.commit()

    run_in_session(control_settings, summary)
    first = owner.get("/wolfbet/alerts/events?limit=3").json()
    assert [e["message"] for e in first["items"]] == ["event 4", "event 3", "event 2"]
    second = owner.get(f"/wolfbet/alerts/events?limit=3&before={first['next_before']}").json()
    assert [e["message"] for e in second["items"]] == ["event 1", "event 0"]
    assert second["next_before"] is None


def test_users_set_their_own_channels_and_quiet_hours(signed_in_as: Any) -> None:
    viewer = signed_in_as(Role.VIEWER, two_factor=False)
    start = viewer.get("/alerts/me/channels").json()
    assert start == {
        "email_enabled": False,
        "telegram_enabled": False,
        "telegram_linked": False,
        "telegram_available": False,
        "push_private": True,
        "push_key": None,
        "digest_hour": None,
        "quiet_start": None,
        "quiet_end": None,
    }
    body = {
        "email_enabled": True,
        "telegram_enabled": True,
        "quiet_start": "23:00:00",
        "quiet_end": "07:00:00",
    }
    saved = viewer.put("/alerts/me/channels", json=body, headers=csrf(viewer)).json()
    assert saved["email_enabled"] is True
    assert saved["telegram_enabled"] is False, "telegram stays off until a chat is linked"
    assert (saved["quiet_start"], saved["quiet_end"]) == ("23:00:00", "07:00:00")
    daily = viewer.put(
        "/alerts/me/channels", json={**body, "digest_hour": 8}, headers=csrf(viewer)
    ).json()
    assert daily["digest_hour"] == 8
    for hour in (-1, 24):
        bad_hour = {**body, "digest_hour": hour}
        assert (
            viewer.put("/alerts/me/channels", json=bad_hour, headers=csrf(viewer)).status_code
            == 422
        )
    for quiet in (("23:00:00", None), ("07:00:00", "07:00:00")):
        bad = {**body, "quiet_start": quiet[0], "quiet_end": quiet[1]}
        response = viewer.put("/alerts/me/channels", json=bad, headers=csrf(viewer))
        assert (response.status_code, response.json()["detail"]) == (422, QUIET_BOTH)


class SilentTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


def test_a_link_opens_the_alert_bot_with_a_code_it_accepts(
    signed_in_as: Any, control_settings: Settings
) -> None:
    viewer = signed_in_as(Role.VIEWER, two_factor=False)
    refused = viewer.post("/alerts/me/telegram-link", headers=csrf(viewer))
    assert (refused.status_code, refused.json()["detail"]) == (409, NO_TELEGRAM)

    viewer.app.state.telegram_bot_username = "casino_alert_bot"
    link = viewer.post("/alerts/me/telegram-link", headers=csrf(viewer)).json()
    url = urlparse(link["url"])
    assert (url.scheme, url.netloc, url.path) == ("https", "t.me", "/casino_alert_bot")
    [code] = parse_qs(url.query)["start"]

    telegram = SilentTelegram()

    async def run(session: AsyncSession) -> None:
        update = {"message": {"chat": {"id": 990011, "type": "private"}, "text": f"/start {code}"}}
        await handle_update(session, update, telegram, datetime.now(UTC))  # type: ignore[arg-type]
        await session.commit()

    run_in_session(control_settings, run)
    after = viewer.get("/alerts/me/channels").json()
    assert (after["telegram_linked"], after["telegram_enabled"], after["telegram_available"]) == (
        True,
        True,
        True,
    )

    async def unlink(session: AsyncSession) -> None:
        await session.execute(delete(UserChannels).where(UserChannels.telegram_chat_id == 990011))
        await session.commit()

    run_in_session(control_settings, unlink)


PUSH_KEY = "B" + "A" * 86


def _browser() -> dict[str, Any]:
    p256dh, auth = browser_keys()
    return {"endpoint": FCM + new_email(), "keys": {"p256dh": p256dh, "auth": auth}}


def test_a_browser_turns_push_on_and_off_for_its_own_user(
    signed_in_as: Any, control_settings: Settings
) -> None:
    viewer = signed_in_as(Role.VIEWER, two_factor=False)
    device = _browser()
    off = viewer.post("/alerts/me/push-subscriptions", json=device, headers=csrf(viewer))
    assert (off.status_code, off.json()["detail"]) == (409, NO_PUSH)

    viewer.app.state.vapid_public_key = PUSH_KEY
    channels = viewer.get("/alerts/me/channels").json()
    assert (channels["push_key"], channels["push_private"]) == (PUSH_KEY, True)
    added = viewer.post("/alerts/me/push-subscriptions", json=device, headers=csrf(viewer))
    assert added.status_code == 204
    again = viewer.post("/alerts/me/push-subscriptions", json=device, headers=csrf(viewer))
    assert again.status_code == 204

    def count(endpoint: str) -> int:
        async def run(session: AsyncSession) -> int:
            rows = await session.scalars(
                select(PushSubscription).where(PushSubscription.endpoint == endpoint)
            )
            return len(list(rows))

        return run_in_session(control_settings, run)

    assert count(device["endpoint"]) == 1
    for bad in (
        {**device, "endpoint": "http://fcm.googleapis.com/x"},
        {**device, "endpoint": "https://internal.example/x"},
        {**device, "keys": {"p256dh": "short", "auth": device["keys"]["auth"]}},
        {**device, "extra": 1},
    ):
        response = viewer.post("/alerts/me/push-subscriptions", json=bad, headers=csrf(viewer))
        assert response.status_code == 422
    removed = viewer.post(
        "/alerts/me/push-subscriptions/remove",
        json={"endpoint": device["endpoint"]},
        headers=csrf(viewer),
    )
    assert removed.status_code == 204
    assert count(device["endpoint"]) == 0


def test_ten_devices_is_the_limit(signed_in_as: Any) -> None:
    viewer = signed_in_as(Role.VIEWER, two_factor=False)
    viewer.app.state.vapid_public_key = PUSH_KEY
    for _ in range(10):
        ok = viewer.post("/alerts/me/push-subscriptions", json=_browser(), headers=csrf(viewer))
        assert ok.status_code == 204
    over = viewer.post("/alerts/me/push-subscriptions", json=_browser(), headers=csrf(viewer))
    assert (over.status_code, over.json()["detail"]) == (422, TOO_MANY_DEVICES)


def test_private_mode_is_saved_and_test_fire_reaches_each_device(
    owner: TestClient, control_settings: Settings
) -> None:
    owner.app.state.vapid_public_key = PUSH_KEY  # type: ignore[attr-defined]
    for _ in range(2):
        owner.post("/alerts/me/push-subscriptions", json=_browser(), headers=csrf(owner))
    body = {"email_enabled": False, "telegram_enabled": False, "push_private": False}
    saved = owner.put("/alerts/me/channels", json=body, headers=csrf(owner)).json()
    assert saved["push_private"] is False
    rule_id = owner.post(RULES, json=DOWN, headers=csrf(owner)).json()["id"]
    fired = owner.post(f"{RULES}/{rule_id}/test", headers=csrf(owner))
    assert fired.json() == {"channels": ["push"]}
    rows = _deliveries(control_settings)
    assert [(channel, message) for _, channel, message in rows] == [
        ("push", "Test alert: Bot down"),
        ("push", "Test alert: Bot down"),
    ]
