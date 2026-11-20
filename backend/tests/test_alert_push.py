"""Web Push alerts: which browsers are accepted, what they are sent, and dead subscriptions."""

import base64
import json
import secrets
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException  # type: ignore[import-untyped]  # ships no type hints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts import push
from app.alerts.channels import deliver_once, fan_out
from app.alerts.models import AlertDelivery, AlertEvent, PushSubscription, UserChannels
from app.alerts.push import (
    PAYLOAD_MAX,
    PRIVATE_BODY,
    TTL_S,
    PushError,
    PushSender,
    Subscription,
    endpoint_allowed,
    keys_valid,
    payload,
)
from app.auth.models import Role, User, UserRole
from tests.conftest import NO_LOGIN, new_email

UTC_ZONE = ZoneInfo("UTC")
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
Sessions = async_sessionmaker[AsyncSession]


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def browser_keys() -> tuple[str, str]:
    point = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    return b64url(point), b64url(secrets.token_bytes(16))


def vapid_pem() -> str:
    return (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )


class Response:
    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {}


class FakePushService:
    """Stands in for `webpush_async`: records each call and answers with a chosen status."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.status: dict[str, int] = {}

    async def __call__(self, info: dict[str, Any], **kwargs: Any) -> None:
        self.calls.append({"endpoint": info["endpoint"], **kwargs})
        status = self.status.get(info["endpoint"], 201)
        if status > 202:
            headers = {"Retry-After": "30"} if status == 429 else {}
            raise WebPushException(f"Push failed: {status}", response=Response(status, headers))

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(c["data"]) for c in self.calls]


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> FakePushService:
    fake = FakePushService()
    monkeypatch.setattr(push, "webpush_async", fake)
    return fake


@pytest.fixture
def sender() -> PushSender:
    return PushSender(vapid_pem(), "no-reply@example.test")


FCM = "https://fcm.googleapis.com/fcm/send/"


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        (FCM + "abc", True),
        ("https://updates.push.services.mozilla.com/wpush/v2/abc", True),
        ("https://web.push.apple.com/QFOo", True),
        ("https://wns2-db5p.notify.windows.com/w/?token=x", True),
        ("http://fcm.googleapis.com/fcm/send/abc", False),
        ("https://evil.example/fcm.googleapis.com", False),
        ("https://fcm.googleapis.com.evil.example/x", False),
        ("https://user:pw@fcm.googleapis.com/x", False),
        ("https://fcm.googleapis.com:8443/x", False),
        ("https://169.254.169.254/latest", False),
        ("https://push.apple.com.example/x", False),
        ("https://" + "a" * 3000, False),
        ("not a url", False),
    ],
)
def test_only_known_push_services_over_https_are_accepted(url: str, ok: bool) -> None:
    assert endpoint_allowed(url) is ok


def test_browser_keys_must_have_the_right_shape() -> None:
    p256dh, auth = browser_keys()
    assert keys_valid(p256dh, auth)
    assert not keys_valid(p256dh[:-4], auth)
    assert not keys_valid(p256dh, b64url(b"x" * 8))
    assert not keys_valid(p256dh + "=", auth)
    assert not keys_valid("not/base64+", auth)


def test_private_payloads_say_only_that_an_alert_fired() -> None:
    message = "Session loss: profit -0.00100000 on lane 1"
    private = json.loads(payload(message, private=True))
    assert private == {"title": "Casino dashboard", "body": PRIVATE_BODY, "url": "/alerts"}
    assert json.loads(payload(message, private=False))["body"] == message
    huge = payload("é" * 5000, private=False)
    assert len(huge.encode()) <= PAYLOAD_MAX
    assert json.loads(huge)["title"] == "Casino dashboard"


async def test_a_send_is_signed_short_lived_and_errors_are_sorted(
    service: FakePushService, sender: PushSender
) -> None:
    p256dh, auth = browser_keys()
    target = Subscription(FCM + "ok", p256dh, auth)
    await sender.send(target, payload("hi", private=False))
    [call] = service.calls
    assert call["ttl"] == TTL_S
    assert call["vapid_claims"] == {"sub": "mailto:no-reply@example.test"}
    assert call["headers"] == {"Urgency": "high"}

    for status, check in (
        (410, lambda e: e.gone),
        (404, lambda e: e.gone),
        (413, lambda e: e.permanent),
        (429, lambda e: e.retry_after == 30),
        (503, lambda e: not (e.gone or e.permanent)),
    ):
        service.status[target.endpoint] = status
        with pytest.raises(PushError) as caught:
            await sender.send(target, "{}")
        assert check(caught.value), status
        assert target.endpoint not in str(caught.value)

    with pytest.raises(PushError) as refused:
        await sender.send(Subscription("https://evil.example/x", p256dh, auth), "{}")
    assert refused.value.gone
    assert len(service.calls) == 6, "a refused endpoint is never contacted"


@pytest.fixture
async def people(sessions: Sessions) -> AsyncIterator[Callable[..., Any]]:
    created: list[int] = []

    async def make(
        devices: int = 1, *, private: bool | None = None, quiet: tuple[time, time] | None = None
    ) -> tuple[int, list[str]]:
        async with sessions() as db:
            user = User(email=new_email(), password_hash=NO_LOGIN)
            db.add(user)
            await db.flush()
            db.add(UserRole(user_id=user.id, service_id=None, role=Role.VIEWER))
            prefs = UserChannels(
                user_id=user.id,
                quiet_start=quiet[0] if quiet else None,
                quiet_end=quiet[1] if quiet else None,
            )
            if private is not None:
                prefs.push_private = private
            db.add(prefs)
            endpoints = [FCM + secrets.token_hex(8) for _ in range(devices)]
            for endpoint in endpoints:
                p256dh, auth = browser_keys()
                db.add(
                    PushSubscription(user_id=user.id, endpoint=endpoint, p256dh=p256dh, auth=auth)
                )
            await db.commit()
            created.append(user.id)
            return user.id, endpoints

    yield make
    async with sessions() as db:
        for user in await db.scalars(select(User).where(User.id.in_(created))):
            await db.delete(user)
        await db.commit()


async def fire(sessions: Sessions, message: str = "Session loss: profit -0.001 on lane 1") -> int:
    async with sessions() as db:
        event = AlertEvent(service="wolfbet", lane=1, kind="loss", message=message)
        db.add(event)
        await db.flush()
        db.add(AlertDelivery(event_id=event.id, channel="push"))
        await db.commit()
        return event.id


async def run_all(sessions: Sessions, sender: PushSender | None, now: datetime = NOW) -> None:
    async with sessions() as db, db.begin():
        await fan_out(db, now)
    for _ in range(50):
        async with sessions() as db, db.begin():
            if not await deliver_once(db, None, UTC_ZONE, now, sender):
                return


async def rows(sessions: Sessions, event_id: int) -> list[AlertDelivery]:
    async with sessions() as db:
        return list(
            await db.scalars(
                select(AlertDelivery)
                .where(AlertDelivery.event_id == event_id, AlertDelivery.user_id.is_not(None))
                .order_by(AlertDelivery.id)
            )
        )


async def test_each_device_gets_the_alert_privately_by_default(
    sessions: Sessions, people: Any, service: FakePushService, sender: PushSender
) -> None:
    _, endpoints = await people(devices=2)
    _, full = await people(private=False)
    event_id = await fire(sessions)
    await run_all(sessions, sender)
    assert {r.status for r in await rows(sessions, event_id)} == {"sent"}
    by_endpoint = {c["endpoint"]: json.loads(c["data"])["body"] for c in service.calls}
    assert by_endpoint == {
        endpoints[0]: PRIVATE_BODY,
        endpoints[1]: PRIVATE_BODY,
        full[0]: "Session loss: profit -0.001 on lane 1",
    }


async def test_a_gone_subscription_is_deleted_and_the_other_device_still_gets_it(
    sessions: Sessions, people: Any, service: FakePushService, sender: PushSender
) -> None:
    _, (dead, alive) = await people(devices=2)
    service.status[dead] = 410
    event_id = await fire(sessions)
    await run_all(sessions, sender)
    async with sessions() as db:
        left = list(
            await db.scalars(
                select(PushSubscription.endpoint).where(
                    PushSubscription.endpoint.in_([dead, alive])
                )
            )
        )
    assert left == [alive]
    [row] = await rows(sessions, event_id)
    assert row.status == "sent"


async def test_failures_retry_and_wait_when_push_is_off_or_quiet(
    sessions: Sessions, people: Any, service: FakePushService, sender: PushSender
) -> None:
    _, (endpoint,) = await people()
    service.status[endpoint] = 503
    event_id = await fire(sessions)
    await run_all(sessions, sender)
    [row] = await rows(sessions, event_id)
    assert (row.status, row.attempts, row.next_attempt_at) == (
        "pending",
        1,
        NOW + timedelta(seconds=30),
    )

    _, (later,) = await people(quiet=(time(23), time(7)))
    second = await fire(sessions)
    night = datetime(2026, 11, 20, 2, 0, tzinfo=UTC)
    await run_all(sessions, None, night)
    held = [r for r in await rows(sessions, second) if r.status == "pending"]
    assert len(held) == 2, "with no sender configured, push deliveries wait"
    calls = len(service.calls)
    await run_all(sessions, sender, night)
    quiet_row = next(
        r
        for r in await rows(sessions, second)
        if r.subscription_id is not None
        and r.next_attempt_at == datetime(2026, 11, 20, 7, 0, tzinfo=UTC)
    )
    assert quiet_row.status == "pending"
    assert later not in [c["endpoint"] for c in service.calls[calls:]]
