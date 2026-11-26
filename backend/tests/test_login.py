"""Sign-in, sessions, CSRF, rate limits and lockout against real Timescale and Redis."""

import secrets
import statistics
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from redis.exceptions import NoPermissionError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.csrf import CSRF_FAILED
from app.auth.models import ApiSession, User
from app.auth.ratelimit import TOO_MANY, UNAVAILABLE
from app.auth.routes import BAD_LOGIN
from app.auth.sessions import NOT_SIGNED_IN, SESSION_COOKIE
from app.settings import Settings
from tests.conftest import PASSWORD, Account, make_client, run_in_session

WRONG = secrets.token_urlsafe(24)


def _set_user(settings: Settings, user_id: int, **values: object) -> None:
    async def run(session: AsyncSession) -> None:
        await session.execute(update(User).where(User.id == user_id).values(**values))
        await session.commit()

    run_in_session(settings, run)


def _age_sessions(settings: Settings, user_id: int, **values: object) -> None:
    async def run(session: AsyncSession) -> None:
        query = update(ApiSession).where(ApiSession.user_id == user_id).values(**values)
        await session.execute(query)
        await session.commit()

    run_in_session(settings, run)


def _session_count(settings: Settings, user_id: int) -> int:
    async def run(session: AsyncSession) -> int:
        query = select(func.count()).select_from(ApiSession).where(ApiSession.user_id == user_id)
        count: int = await session.scalar(query) or 0
        return count

    return run_in_session(settings, run)


def _login(client: TestClient, email: str, password: str = PASSWORD) -> int:
    return client.post("/auth/login", json={"email": email, "password": password}).status_code


def _csrf(client: TestClient) -> str:
    token: str = client.get("/auth/me").json()["csrf_token"]
    return token


class TestLogin:
    def test_sets_a_hardened_host_cookie(self, client: TestClient, account: Account) -> None:
        response = client.post("/auth/login", json={"email": account.email, "password": PASSWORD})
        assert response.status_code == 204
        cookie = response.headers["set-cookie"]
        assert cookie.startswith(f"{SESSION_COOKIE}=")
        for flag in ("Secure", "HttpOnly", "SameSite=strict", "Path=/", "Max-Age=7776000"):
            assert flag in cookie
        assert "Domain" not in cookie

    def test_me_returns_email_roles_and_csrf(self, client: TestClient, account: Account) -> None:
        assert _login(client, account.email.upper()) == 204
        body = client.get("/auth/me").json()
        assert body["email"] == account.email
        assert body["roles"] == [{"service": None, "role": "viewer", "lane": None, "key_ref": None}]
        assert len(body["csrf_token"]) == 64

    @pytest.mark.parametrize("case", ["wrong_password", "unknown_email", "disabled"])
    def test_failures_look_the_same(
        self, app_settings: Settings, client: TestClient, account: Account, case: str
    ) -> None:
        email, password = account.email, PASSWORD
        if case == "wrong_password":
            password = WRONG
        elif case == "unknown_email":
            email = "nobody@example.test"
        else:
            _set_user(app_settings, account.id, disabled=True)
        response = client.post("/auth/login", json={"email": email, "password": password})
        assert response.status_code == 401
        assert response.json() == {"detail": BAD_LOGIN}

    def test_requires_an_allowed_origin(self, client: TestClient, account: Account) -> None:
        body = {"email": account.email, "password": PASSWORD}
        foreign = client.post("/auth/login", json=body, headers={"origin": "https://evil.test"})
        assert foreign.status_code == 403
        assert foreign.json() == {"detail": CSRF_FAILED}
        del client.headers["origin"]
        assert client.post("/auth/login", json=body).status_code == 403

    def test_unknown_email_takes_about_as_long_as_a_wrong_password(
        self, client: TestClient, account: Account
    ) -> None:
        def timed(email: str) -> float:
            start = time.perf_counter()
            assert _login(client, email, WRONG) == 401
            return time.perf_counter() - start

        known = [timed(account.email) for _ in range(5)]
        unknown = [timed(f"nobody-{i}@example.test") for i in range(5)]
        ratio = statistics.median(unknown) / statistics.median(known)
        assert 0.5 < ratio < 2.0


class TestRateLimit:
    def test_sixth_attempt_on_one_account_is_refused(
        self, client: TestClient, account: Account
    ) -> None:
        for _ in range(5):
            assert _login(client, account.email, WRONG) == 401
        response = client.post("/auth/login", json={"email": account.email, "password": PASSWORD})
        assert response.status_code == 429
        assert response.json() == {"detail": TOO_MANY}

    def test_twenty_first_attempt_from_one_ip_is_refused(self, client: TestClient) -> None:
        for i in range(20):
            assert _login(client, f"spray-{i}@example.test") == 401
        assert _login(client, "spray-20@example.test") == 429

    def test_redis_down_fails_closed(self, app_settings: Settings, account: Account) -> None:
        broken = app_settings.model_copy(update={"redis_port": 1})
        with make_client(broken) as client:
            response = client.post(
                "/auth/login", json={"email": account.email, "password": PASSWORD}
            )
        assert response.status_code == 503
        assert response.json() == {"detail": UNAVAILABLE}


class TestLockout:
    def test_tenth_failure_locks_even_the_right_password(
        self, app_settings: Settings, client: TestClient, account: Account
    ) -> None:
        _set_user(app_settings, account.id, failed_count=9)
        assert _login(client, account.email, WRONG) == 401
        response = client.post("/auth/login", json={"email": account.email, "password": PASSWORD})
        assert response.status_code == 401
        assert response.json() == {"detail": BAD_LOGIN}

    def test_lock_expires(
        self, app_settings: Settings, client: TestClient, account: Account
    ) -> None:
        past = datetime.now(UTC) - timedelta(seconds=1)
        _set_user(app_settings, account.id, failed_count=0, locked_until=past)
        assert _login(client, account.email) == 204


class TestCsrf:
    def test_unsafe_request_needs_the_session_token(
        self, client: TestClient, account: Account
    ) -> None:
        assert _login(client, account.email) == 204
        token = _csrf(client)
        missing = client.post("/auth/logout")
        assert missing.status_code == 403
        assert missing.json() == {"detail": CSRF_FAILED}
        assert client.post("/auth/logout", headers={"x-csrf-token": "0" * 64}).status_code == 403
        assert client.post("/auth/logout", headers={"x-csrf-token": token}).status_code == 204
        assert client.get("/auth/me").status_code == 401


class TestSessions:
    def test_login_replaces_a_planted_session(
        self, app_settings: Settings, client: TestClient, account: Account
    ) -> None:
        assert _login(client, account.email) == 204
        first = client.cookies[SESSION_COOKIE]
        assert _login(client, account.email) == 204
        second = client.cookies[SESSION_COOKIE]
        assert first != second
        assert _session_count(app_settings, account.id) == 1
        with make_client(app_settings) as other:
            other.cookies[SESSION_COOKIE] = first
            assert other.get("/auth/me").status_code == 401

    @pytest.mark.parametrize(
        "aged",
        [
            {"created_at": datetime.now(UTC) - timedelta(days=90, minutes=1)},
            {"last_seen_at": datetime.now(UTC) - timedelta(days=30, minutes=1)},
        ],
        ids=["absolute", "idle"],
    )
    def test_expired_session_is_refused_and_deleted(
        self,
        app_settings: Settings,
        client: TestClient,
        account: Account,
        aged: dict[str, datetime],
    ) -> None:
        assert _login(client, account.email) == 204
        _age_sessions(app_settings, account.id, **aged)
        response = client.get("/auth/me")
        assert response.status_code == 401
        assert response.json() == {"detail": NOT_SIGNED_IN}
        assert _session_count(app_settings, account.id) == 0

    def test_activity_keeps_a_long_session_alive(
        self, app_settings: Settings, client: TestClient, account: Account
    ) -> None:
        assert _login(client, account.email) == 204
        now = datetime.now(UTC)
        _age_sessions(
            app_settings,
            account.id,
            created_at=now - timedelta(days=60),
            last_seen_at=now - timedelta(days=29),
        )
        assert client.get("/auth/me").status_code == 200

        async def last_seen(session: AsyncSession) -> datetime | None:
            query = select(ApiSession.last_seen_at).where(ApiSession.user_id == account.id)
            return await session.scalar(query)

        seen = run_in_session(app_settings, last_seen)
        assert seen is not None
        assert now - timedelta(seconds=5) < seen

    def test_disabling_a_user_ends_their_session(
        self, app_settings: Settings, client: TestClient, account: Account
    ) -> None:
        assert _login(client, account.email) == 204
        _set_user(app_settings, account.id, disabled=True)
        assert client.get("/auth/me").status_code == 401

    def test_logout_all_ends_every_session(
        self, app_settings: Settings, client: TestClient, account: Account
    ) -> None:
        with make_client(app_settings) as phone:
            assert _login(client, account.email) == 204
            assert _login(phone, account.email) == 204
            assert _session_count(app_settings, account.id) == 2
            token = _csrf(client)
            response = client.post("/auth/logout-all", headers={"x-csrf-token": token})
            assert response.status_code == 204
            assert phone.get("/auth/me").status_code == 401
        assert _session_count(app_settings, account.id) == 0


class TestRedisAcl:
    def _redis(self, settings: Settings) -> Redis:
        return Redis.from_url(settings.redis_url)

    def test_dashboard_user_may_write_rate_limit_keys(self, app_settings: Settings) -> None:
        with self._redis(app_settings) as redis:
            assert redis.zadd("auth:acl-check", {"x": 1}) == 1
            redis.pexpire("auth:acl-check", 1000)

    @pytest.mark.parametrize("command", ["zadd", "xadd"])
    def test_dashboard_user_cannot_write_event_streams(
        self, app_settings: Settings, command: str
    ) -> None:
        with self._redis(app_settings) as redis, pytest.raises(NoPermissionError):
            if command == "zadd":
                redis.zadd("wolfbet:events", {"x": 1})
            else:
                redis.xadd("wolfbet:events", {"x": "1"})
