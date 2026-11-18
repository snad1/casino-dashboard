"""Invites, password reset, notices, the outbox worker and the two mail transports."""

import asyncio
import json
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.invites import EMAIL_TAKEN, INVITE_INVALID
from app.auth.models import Invite, PasswordReset, Role, User
from app.auth.permissions import FORBIDDEN, TWO_FACTOR_NEEDED
from app.auth.reset import RESET_INVALID, RESET_SENT
from app.mail import templates
from app.mail.mailers import BREVO_URL, BrevoApiMailer, MailError, Message, SmtpMailer
from app.mail.models import DEAD, PENDING, SENT, OutboxMessage
from app.mail.outbox import enqueue
from app.mail.worker import MAX_ATTEMPTS, process_once
from app.settings import Settings
from tests.conftest import PASSWORD, Account, make_client, make_user, new_email, run_in_session

NEW_PASSWORD = "lantern-orbit-" + uuid.uuid4().hex[:12]
TOKEN = re.compile(r"#token=([A-Za-z0-9_-]+)")


def _outbox(settings: Settings, to_email: str) -> list[OutboxMessage]:
    async def run(session: AsyncSession) -> list[OutboxMessage]:
        query = select(OutboxMessage).where(OutboxMessage.to_email == to_email)
        return list(await session.scalars(query.order_by(OutboxMessage.id)))

    return run_in_session(settings, run)


def _token_sent_to(settings: Settings, to_email: str) -> str:
    body = _outbox(settings, to_email)[-1].text_body or ""
    match = TOKEN.search(body)
    assert match, body
    return match.group(1)


class TestInvites:
    def test_accepted_invite_sets_password_and_role(
        self, app_settings: Settings, as_user: Any
    ) -> None:
        admin = make_user(app_settings, Role.ADMIN, two_factor=True)
        email = new_email()
        response = as_user(admin).post("/auth/invites", json={"email": email, "role": "operator"})
        assert response.status_code == 204
        mail = _outbox(app_settings, email)[-1]
        assert mail.subject == "You're invited to the casino dashboard"
        assert f"https://{app_settings.public_host}/invite#token=" in (mail.text_body or "")
        token = _token_sent_to(app_settings, email)
        with make_client(app_settings) as guest:
            body = {"token": token, "password": NEW_PASSWORD}
            assert guest.post("/auth/invites/accept", json=body).status_code == 204
            again = guest.post("/auth/invites/accept", json=body)
            assert again.status_code == 400
            assert again.json() == {"detail": INVITE_INVALID}
            login = {"email": email, "password": NEW_PASSWORD}
            assert guest.post("/auth/login", json=login).status_code == 204
            assert guest.get("/auth/me").json()["roles"] == [{"service": None, "role": "operator"}]

    def test_expired_invite_is_refused(self, app_settings: Settings, as_user: Any) -> None:
        admin = make_user(app_settings, Role.ADMIN, two_factor=True)
        email = new_email()
        as_user(admin).post("/auth/invites", json={"email": email, "role": "viewer"})
        token = _token_sent_to(app_settings, email)

        async def expire(session: AsyncSession) -> None:
            past = datetime.now(UTC) - timedelta(seconds=1)
            query = update(Invite).where(Invite.email == email)
            await session.execute(query.values(expires_at=past))
            await session.commit()

        run_in_session(app_settings, expire)
        with make_client(app_settings) as guest:
            body = {"token": token, "password": NEW_PASSWORD}
            assert guest.post("/auth/invites/accept", json=body).status_code == 400

    def test_weak_password_is_refused_and_invite_stays_usable(
        self, app_settings: Settings, as_user: Any
    ) -> None:
        admin = make_user(app_settings, Role.ADMIN, two_factor=True)
        email = new_email()
        as_user(admin).post("/auth/invites", json={"email": email, "role": "viewer"})
        token = _token_sent_to(app_settings, email)
        with make_client(app_settings) as guest:
            weak = guest.post("/auth/invites/accept", json={"token": token, "password": "short"})
            assert weak.status_code == 400
            assert "at least 12 characters" in weak.json()["detail"]
            body = {"token": token, "password": NEW_PASSWORD}
            assert guest.post("/auth/invites/accept", json=body).status_code == 204

    @pytest.mark.parametrize(
        ("role", "two_factor", "body", "status", "detail"),
        [
            (Role.ADMIN, True, {"role": "owner"}, 422, None),
            (Role.ADMIN, True, {"role": "viewer", "service": "no-such"}, 422, None),
            (Role.VIEWER, False, {"role": "viewer"}, 403, FORBIDDEN),
            (Role.ADMIN, False, {"role": "viewer"}, 403, TWO_FACTOR_NEEDED),
        ],
        ids=["owner-role", "unknown-service", "viewer-inviter", "admin-without-2fa"],
    )
    def test_refusals(
        self,
        app_settings: Settings,
        as_user: Any,
        role: Role,
        two_factor: bool,
        body: dict[str, str],
        status: int,
        detail: str | None,
    ) -> None:
        inviter = make_user(app_settings, role, two_factor=two_factor)
        email = new_email()
        response = as_user(inviter).post("/auth/invites", json={"email": email, **body})
        assert response.status_code == status
        if detail:
            assert response.json() == {"detail": detail}
        assert _outbox(app_settings, email) == []

    def test_existing_account_is_refused(
        self, app_settings: Settings, as_user: Any, account: Account
    ) -> None:
        admin = make_user(app_settings, Role.ADMIN, two_factor=True)
        response = as_user(admin).post(
            "/auth/invites", json={"email": account.email.upper(), "role": "viewer"}
        )
        assert response.status_code == 409
        assert response.json() == {"detail": EMAIL_TAKEN}


class TestPasswordReset:
    def test_answer_is_the_same_for_unknown_and_known_emails(
        self, app_settings: Settings, client: TestClient, account: Account
    ) -> None:
        unknown = client.post("/auth/password-reset", json={"email": "nobody@example.test"})
        known = client.post("/auth/password-reset", json={"email": account.email})
        assert unknown.status_code == known.status_code == 202
        assert unknown.json() == known.json() == {"detail": RESET_SENT}
        assert _outbox(app_settings, "nobody@example.test") == []
        assert len(_outbox(app_settings, account.email)) == 1

    def test_reset_sets_password_and_signs_out_everywhere(
        self, app_settings: Settings, client: TestClient, account: Account
    ) -> None:
        old = {"email": account.email, "password": PASSWORD}
        assert client.post("/auth/login", json=old).status_code == 204
        with make_client(app_settings) as stranger:
            stranger.post("/auth/password-reset", json={"email": account.email})
            token = _token_sent_to(app_settings, account.email)
            body = {"token": token, "password": NEW_PASSWORD}
            assert stranger.post("/auth/password-reset/confirm", json=body).status_code == 204
            reused = stranger.post("/auth/password-reset/confirm", json=body)
            assert reused.status_code == 400
            assert reused.json() == {"detail": RESET_INVALID}
        assert client.get("/auth/me").status_code == 401
        with make_client(app_settings) as fresh:
            assert fresh.post("/auth/login", json=old).status_code == 401
            new = {"email": account.email, "password": NEW_PASSWORD}
            assert fresh.post("/auth/login", json=new).status_code == 204

    def test_expired_reset_link_is_refused(
        self, app_settings: Settings, client: TestClient, account: Account
    ) -> None:
        client.post("/auth/password-reset", json={"email": account.email})
        token = _token_sent_to(app_settings, account.email)

        async def expire(session: AsyncSession) -> None:
            past = datetime.now(UTC) - timedelta(seconds=1)
            query = update(PasswordReset).where(PasswordReset.user_id == account.id)
            await session.execute(query.values(expires_at=past))
            await session.commit()

        run_in_session(app_settings, expire)
        body = {"token": token, "password": NEW_PASSWORD}
        assert client.post("/auth/password-reset/confirm", json=body).status_code == 400


class TestNotices:
    def test_sign_in_sends_a_notice(
        self, app_settings: Settings, client: TestClient, account: Account
    ) -> None:
        client.post("/auth/login", json={"email": account.email, "password": PASSWORD})
        subjects = [m.subject for m in _outbox(app_settings, account.email)]
        assert subjects == ["New sign-in to the casino dashboard"]

    def test_lockout_sends_a_notice(
        self, app_settings: Settings, client: TestClient, account: Account
    ) -> None:
        async def nearly(session: AsyncSession) -> None:
            await session.execute(update(User).where(User.id == account.id).values(failed_count=9))
            await session.commit()

        run_in_session(app_settings, nearly)
        client.post("/auth/login", json={"email": account.email, "password": "wrong-" + PASSWORD})
        subjects = [m.subject for m in _outbox(app_settings, account.email)]
        assert subjects == ["Your casino dashboard account is locked"]


class FakeMailer:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[Message] = []

    async def send(self, message: Message) -> None:
        if self.fail:
            raise MailError("brevo refused the message: HTTP 503")
        self.sent.append(message)


@pytest.fixture
def worker_db(app_settings: Settings) -> Iterator[async_sessionmaker[AsyncSession]]:
    """Only this test's row is due, so other tests' queued notices never get in the way."""

    async def park(session: AsyncSession) -> None:
        later = datetime.now(UTC) + timedelta(days=1)
        query = update(OutboxMessage).where(OutboxMessage.status == PENDING)
        await session.execute(query.values(next_attempt_at=later))
        await session.commit()

    run_in_session(app_settings, park)
    engine = create_async_engine(app_settings.database_url, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)

    async def clear(session: AsyncSession) -> None:
        await session.execute(delete(OutboxMessage))
        await session.commit()

    run_in_session(app_settings, clear)


def _queue(settings: Settings, email: str, **values: object) -> None:
    async def run(session: AsyncSession) -> None:
        enqueue(session, email, templates.password_reset("https://x.test/reset#token=abc"))
        await session.flush()
        if values:
            query = update(OutboxMessage).where(OutboxMessage.to_email == email)
            await session.execute(query.values(**values))
        await session.commit()

    run_in_session(settings, run)


def _pass(sessionmaker: async_sessionmaker[AsyncSession], mailer: FakeMailer) -> bool:
    return asyncio.run(process_once(sessionmaker, mailer))


class TestWorker:
    def test_sent_mail_loses_its_body(
        self, app_settings: Settings, worker_db: async_sessionmaker[AsyncSession]
    ) -> None:
        email = new_email()
        _queue(app_settings, email)
        mailer = FakeMailer()
        assert _pass(worker_db, mailer) is True
        assert _pass(worker_db, mailer) is False
        assert [m.to_email for m in mailer.sent] == [email]
        row = _outbox(app_settings, email)[0]
        assert (row.status, row.text_body, row.html_body) == (SENT, None, None)
        assert row.sent_at is not None

    def test_failure_backs_off_then_goes_dead(
        self, app_settings: Settings, worker_db: async_sessionmaker[AsyncSession]
    ) -> None:
        email = new_email()
        _queue(app_settings, email)
        assert _pass(worker_db, FakeMailer(fail=True)) is True
        row = _outbox(app_settings, email)[0]
        assert (row.status, row.attempts) == (PENDING, 1)
        assert row.next_attempt_at > datetime.now(UTC) + timedelta(seconds=20)
        assert row.last_error == "brevo refused the message: HTTP 503"

        async def due_on_last_try(session: AsyncSession) -> None:
            query = update(OutboxMessage).where(OutboxMessage.to_email == email)
            await session.execute(
                query.values(attempts=MAX_ATTEMPTS - 1, next_attempt_at=datetime.now(UTC))
            )
            await session.commit()

        run_in_session(app_settings, due_on_last_try)
        assert _pass(worker_db, FakeMailer(fail=True)) is True
        row = _outbox(app_settings, email)[0]
        assert (row.status, row.text_body, row.html_body) == (DEAD, None, None)


MESSAGE = Message("to@example.test", "Subject", "plain", "<p>html</p>")


class TestTransports:
    async def test_brevo_posts_the_message_with_the_key_header(self) -> None:
        seen: list[httpx2.Request] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(request)
            return httpx2.Response(201, json={"messageId": "x"})

        transport = httpx2.MockTransport(handler)
        mailer = BrevoApiMailer("xkeysib-test", "from@example.test", "Dash", transport)
        await mailer.send(MESSAGE)
        request = seen[0]
        assert str(request.url) == BREVO_URL
        assert request.headers["api-key"] == "xkeysib-test"
        payload = json.loads(request.content)
        assert payload["to"] == [{"email": "to@example.test"}]
        assert payload["sender"] == {"email": "from@example.test", "name": "Dash"}
        assert (payload["textContent"], payload["htmlContent"]) == ("plain", "<p>html</p>")

    @pytest.mark.parametrize("outcome", ["http-500", "unreachable"])
    async def test_brevo_failures_raise_mail_error_without_the_key(self, outcome: str) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            if outcome == "unreachable":
                raise httpx2.ConnectError("down", request=request)
            return httpx2.Response(500)

        transport = httpx2.MockTransport(handler)
        mailer = BrevoApiMailer("xkeysib-test", "f@example.test", "D", transport)
        with pytest.raises(MailError) as caught:
            await mailer.send(MESSAGE)
        assert "xkeysib" not in str(caught.value)

    async def test_smtp_refuses_a_relay_without_starttls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        logged_in: list[bool] = []

        class PlainRelay:
            def __init__(self, *_: object, **__: object) -> None: ...
            def __enter__(self) -> "PlainRelay":
                return self

            def __exit__(self, *_: object) -> None: ...
            def ehlo(self) -> None: ...
            def has_extn(self, name: str) -> bool:
                return False

            def login(self, *_: object) -> None:
                logged_in.append(True)

        monkeypatch.setattr("smtplib.SMTP", PlainRelay)
        mailer = SmtpMailer("smtp.example.test", 587, "u", "p", "f@example.test", "D")
        with pytest.raises(MailError, match="STARTTLS"):
            await mailer.send(MESSAGE)
        assert logged_in == []


def test_templates_escape_html() -> None:
    rendered = templates.invite('https://x.test/invite#token="><b>', "viewer", "<b>Eve</b>")
    assert "<b>Eve</b>" not in rendered.html
    assert "&lt;b&gt;Eve&lt;/b&gt;" in rendered.html
    assert 'href="https://x.test/invite#token=&quot;&gt;&lt;b&gt;"' in rendered.html
    assert "<b>Eve</b> invited you" in rendered.text
