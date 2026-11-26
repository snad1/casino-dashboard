"""Authenticator codes: enrolment, two-step sign-in, replay, recovery codes, step-up, disable."""

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast

import pytest
from cryptography.exceptions import InvalidTag
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pyotp import random_base32
from pyotp.totp import TOTP
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import ApiSession, RecoveryCode, Totp, User
from app.auth.ratelimit import TOO_MANY_CODES
from app.auth.routes import BAD_LOGIN, CODE_NEEDED
from app.auth.sessions import SESSION_COOKIE
from app.auth.stepup import STEP_UP_NEEDED, require_fresh
from app.auth.totp import (
    ALREADY_ON,
    BAD_CODE,
    DISABLE_REFUSED,
    STEP_SECONDS,
    decrypt_secret,
    encrypt_secret,
    matching_step,
)
from app.settings import Settings
from tests.conftest import PASSWORD, Account, make_client, run_in_session


@dataclass
class Enrolled:
    secret: str
    recovery_codes: list[str]


def _step() -> int:
    return int(time.time() // STEP_SECONDS)


def _post(client: TestClient, path: str, body: dict[str, str] | None = None) -> int:
    token = client.get("/auth/me").json()["csrf_token"]
    return client.post(path, json=body, headers={"x-csrf-token": token}).status_code


def _login(client: TestClient, email: str, code: str | None = None) -> int:
    body = {"email": email, "password": PASSWORD}
    if code is not None:
        body["code"] = code
    return client.post("/auth/login", json=body).status_code


def _enrol(client: TestClient, email: str) -> Enrolled:
    assert _login(client, email) == 204
    token = client.get("/auth/me").json()["csrf_token"]
    enrolment = client.post("/auth/totp/enrol", headers={"x-csrf-token": token}).json()
    secret = enrolment["secret"]
    response = client.post(
        "/auth/totp/verify",
        json={"code": TOTP(secret).now()},
        headers={"x-csrf-token": token},
    )
    assert response.status_code == 200
    return Enrolled(secret, response.json()["recovery_codes"])


def _rows[T](settings: Settings, model: type[T], user_id: int) -> int:
    async def run(session: AsyncSession) -> int:
        column = model.user_id  # type: ignore[attr-defined]
        count: int = (
            await session.scalar(select(func.count()).select_from(model).where(column == user_id))
            or 0
        )
        return count

    return run_in_session(settings, run)


class TestEnrolment:
    def test_enrol_gives_an_otpauth_uri_and_stays_off_until_verified(
        self, client: TestClient, account: Account
    ) -> None:
        assert _login(client, account.email) == 204
        token = client.get("/auth/me").json()["csrf_token"]
        body = client.post("/auth/totp/enrol", headers={"x-csrf-token": token}).json()
        assert body["otpauth_uri"].startswith("otpauth://totp/")
        assert body["secret"] in body["otpauth_uri"]
        assert client.get("/auth/me").json()["totp_enabled"] is False
        wrong = client.post(
            "/auth/totp/verify", json={"code": "000000"}, headers={"x-csrf-token": token}
        )
        assert wrong.status_code == 400
        assert wrong.json() == {"detail": BAD_CODE}
        assert client.get("/auth/me").json()["totp_enabled"] is False

    def test_verify_turns_it_on_rotates_the_session_and_shows_codes_once(
        self, client: TestClient, account: Account
    ) -> None:
        assert _login(client, account.email) == 204
        before = client.cookies[SESSION_COOKIE]
        enrolled = _enrol(client, account.email)
        assert len(enrolled.recovery_codes) == 10
        assert len(set(enrolled.recovery_codes)) == 10
        assert client.cookies[SESSION_COOKIE] != before
        me = client.get("/auth/me").json()
        assert me["totp_enabled"] is True
        response = client.post("/auth/totp/enrol", headers={"x-csrf-token": me["csrf_token"]})
        assert response.status_code == 409
        assert response.json() == {"detail": ALREADY_ON}

    def test_secret_is_encrypted_and_bound_to_the_user(
        self, app_settings: Settings, client: TestClient, account: Account
    ) -> None:
        enrolled = _enrol(client, account.email)

        async def blob(session: AsyncSession) -> bytes:
            data: bytes = (
                await session.scalar(
                    select(Totp.secret_ciphertext).where(Totp.user_id == account.id)
                )
                or b""
            )
            return data

        stored = run_in_session(app_settings, blob)
        key = app_settings.totp_key.get_secret_value()
        assert enrolled.secret.encode() not in stored
        assert decrypt_secret(key, account.id, stored) == enrolled.secret
        with pytest.raises(InvalidTag):
            decrypt_secret(key, account.id + 1, stored)
        assert decrypt_secret(key, 7, encrypt_secret(key, 7, "ABC")) == "ABC"


class TestCodes:
    def test_window_is_one_step_either_side(self) -> None:
        secret = random_base32()
        now = 1_800_000_000.0
        step = int(now // STEP_SECONDS)
        otp = TOTP(secret)
        for offset in (-1, 0, 1):
            assert matching_step(secret, otp.generate_otp(step + offset), now) == step + offset
        for offset in (-2, 2):
            assert matching_step(secret, otp.generate_otp(step + offset), now) is None

    def test_login_asks_for_a_code_then_accepts_it_once(
        self, client: TestClient, account: Account
    ) -> None:
        enrolled = _enrol(client, account.email)
        response = client.post("/auth/login", json={"email": account.email, "password": PASSWORD})
        assert response.status_code == 401
        assert response.json() == {"detail": CODE_NEEDED, "totp_required": True}
        used_at_verify = TOTP(enrolled.secret).now()
        assert _login(client, account.email, used_at_verify) == 401
        fresh = TOTP(enrolled.secret).generate_otp(_step() + 1)
        assert _login(client, account.email, fresh) == 204
        replay = client.post(
            "/auth/login", json={"email": account.email, "password": PASSWORD, "code": fresh}
        )
        assert replay.status_code == 401
        assert replay.json() == {"detail": BAD_LOGIN}

    def test_recovery_code_works_once(self, client: TestClient, account: Account) -> None:
        code = _enrol(client, account.email).recovery_codes[0]
        assert _login(client, account.email, code.upper()) == 204
        assert _login(client, account.email, code) == 401

    def test_code_checks_are_rate_limited(self, client: TestClient, account: Account) -> None:
        _enrol(client, account.email)
        token = client.get("/auth/me").json()["csrf_token"]
        statuses = [
            client.post(
                "/auth/step-up", json={"code": "000000"}, headers={"x-csrf-token": token}
            ).status_code
            for _ in range(4)
        ]
        # The enrolment's own verify was the first of five checks this minute.
        assert statuses == [400] * 4
        last = client.post(
            "/auth/step-up", json={"code": "000000"}, headers={"x-csrf-token": token}
        )
        assert last.status_code == 429
        assert last.json() == {"detail": TOO_MANY_CODES}


class TestStepUp:
    @staticmethod
    def _danger_app(client: TestClient) -> FastAPI:
        app = cast(FastAPI, client.app)

        @app.post("/danger", status_code=204)
        async def danger(user: Annotated[User, Depends(require_fresh())]) -> None:
            return None

        return app

    @staticmethod
    def _age(settings: Settings, user_id: int, **values: datetime | None) -> None:
        async def age(session: AsyncSession) -> None:
            query = update(ApiSession).where(ApiSession.user_id == user_id)
            await session.execute(query.values(**values))
            await session.commit()

        run_in_session(settings, age)

    @staticmethod
    def _used_at(settings: Settings, user_id: int) -> datetime | None:
        async def read(session: AsyncSession) -> datetime | None:
            query = select(ApiSession.step_up_used_at).where(ApiSession.user_id == user_id)
            return await session.scalar(query)

        return run_in_session(settings, read)

    def test_a_code_covers_a_day_after_its_last_use(
        self, app_settings: Settings, account: Account
    ) -> None:
        with make_client(app_settings) as client:
            self._danger_app(client)
            enrolled = _enrol(client, account.email)
            now = datetime.now(UTC)
            self._age(app_settings, account.id, step_up_at=now - timedelta(hours=23))
            assert _post(client, "/danger") == 204
            used = self._used_at(app_settings, account.id)
            assert used is not None
            assert now - timedelta(seconds=5) < used
            self._age(
                app_settings,
                account.id,
                step_up_at=now - timedelta(hours=30),
                step_up_used_at=now - timedelta(hours=25),
            )
            stale = client.post(
                "/danger", headers={"x-csrf-token": client.get("/auth/me").json()["csrf_token"]}
            )
            assert stale.status_code == 403
            assert stale.json() == {"detail": STEP_UP_NEEDED, "step_up_required": True}
            assert _post(client, "/auth/step-up", {"code": enrolled.recovery_codes[0]}) == 204
            assert _post(client, "/danger") == 204

    def test_daily_use_still_asks_again_after_a_week(
        self, app_settings: Settings, account: Account
    ) -> None:
        with make_client(app_settings) as client:
            self._danger_app(client)
            _enrol(client, account.email)
            now = datetime.now(UTC)
            self._age(
                app_settings,
                account.id,
                step_up_at=now - timedelta(days=7, minutes=1),
                step_up_used_at=now - timedelta(hours=1),
            )
            assert _post(client, "/danger") == 403

    def test_password_only_session_is_never_fresh(
        self, app_settings: Settings, account: Account
    ) -> None:
        with make_client(app_settings) as client:
            app = cast(FastAPI, client.app)

            @app.post("/danger", status_code=204)
            async def danger(user: Annotated[User, Depends(require_fresh())]) -> None:
                return None

            assert _login(client, account.email) == 204
            assert _post(client, "/danger") == 403


class TestDisable:
    def test_needs_password_and_code(
        self, app_settings: Settings, client: TestClient, account: Account
    ) -> None:
        enrolled = _enrol(client, account.email)
        token = client.get("/auth/me").json()["csrf_token"]
        wrong = client.post(
            "/auth/totp/disable",
            json={"password": "not-my-password", "code": enrolled.recovery_codes[0]},
            headers={"x-csrf-token": token},
        )
        assert wrong.status_code == 401
        assert wrong.json() == {"detail": DISABLE_REFUSED}
        body = {"password": PASSWORD, "code": enrolled.recovery_codes[0]}
        assert _post(client, "/auth/totp/disable", body) == 204
        assert _rows(app_settings, Totp, account.id) == 0
        assert _rows(app_settings, RecoveryCode, account.id) == 0
        assert _login(client, account.email) == 204
