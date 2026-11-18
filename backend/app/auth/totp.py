"""Authenticator-app codes (TOTP) and one-time recovery codes.

The TOTP secret is stored AES-GCM encrypted with a key from the `totp_key` secret file, bound
to the user id so a row copied to another user does not decrypt. A code is accepted for the
current 30 s step and one step either side, and never twice: the last accepted step is kept
and only a later step passes, checked and stored in one statement so two parallel requests
cannot both use the same code.
"""

import hashlib
import hmac
import os
import secrets
import time
from datetime import UTC, datetime
from typing import Annotated

import pyotp
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from pyotp.totp import TOTP
from sqlalchemy import delete, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.auth.models import RecoveryCode, Totp, User
from app.auth.passwords import verify_password
from app.auth.ratelimit import check_code_rate
from app.auth.sessions import current_user, new_session, revoke, set_session_cookie
from app.db.engine import get_session

ISSUER = "Casino dashboard"
STEP_SECONDS = 30
WINDOW_STEPS = 1
RECOVERY_CODE_COUNT = 10
ALREADY_ON = "Two-factor authentication is already on."
NOT_STARTED = "Start two-factor setup first."
BAD_CODE = "That code is not valid. Try the newest code from your app."
DISABLE_REFUSED = "Password or code is incorrect."

router = APIRouter(prefix="/auth/totp", tags=["auth"])
Db = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(current_user)]


def _aead(secret_key: str) -> AESGCM:
    return AESGCM(hashlib.sha256(secret_key.encode()).digest())


def encrypt_secret(secret_key: str, user_id: int, secret: str) -> bytes:
    nonce = os.urandom(12)
    return nonce + _aead(secret_key).encrypt(nonce, secret.encode(), str(user_id).encode())


def decrypt_secret(secret_key: str, user_id: int, blob: bytes) -> str:
    plain = _aead(secret_key).decrypt(blob[:12], blob[12:], str(user_id).encode())
    return plain.decode()


def _recovery_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _normalise(code: str) -> str:
    return "".join(code.split()).lower()


def new_recovery_codes() -> list[str]:
    """Ten codes like `k3j9d-x0p2m`, 50 random bits each."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return [
        "-".join("".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(2))
        for _ in range(RECOVERY_CODE_COUNT)
    ]


def matching_step(secret: str, code: str, now: float | None = None) -> int | None:
    """The time step `code` belongs to, within one step of now, or None."""
    otp = TOTP(secret)
    current = int((time.time() if now is None else now) // STEP_SECONDS)
    for step in range(current - WINDOW_STEPS, current + WINDOW_STEPS + 1):
        if hmac.compare_digest(otp.generate_otp(step), code):
            return step
    return None


async def is_enabled(db: AsyncSession, user_id: int) -> bool:
    query = select(Totp.user_id).where(Totp.user_id == user_id, Totp.enabled_at.is_not(None))
    return await db.scalar(query) is not None


async def verify_code(db: AsyncSession, secret_key: str, user_id: int, code: str) -> bool:
    """Accept an unused TOTP code or an unused recovery code, and burn it. Caller commits."""
    code = _normalise(code)
    if code.isdigit() and len(code) == 6:
        row = await db.scalar(
            select(Totp).where(Totp.user_id == user_id, Totp.enabled_at.is_not(None))
        )
        if row is None:
            return False
        step = matching_step(decrypt_secret(secret_key, user_id, row.secret_ciphertext), code)
        if step is None:
            return False
        accepted = await db.scalar(
            update(Totp)
            .where(
                Totp.user_id == user_id,
                or_(Totp.last_counter.is_(None), Totp.last_counter < step),
            )
            .values(last_counter=step)
            .returning(Totp.user_id)
        )
        return accepted is not None
    used = await db.scalar(
        update(RecoveryCode)
        .where(
            RecoveryCode.user_id == user_id,
            RecoveryCode.code_hash == _recovery_hash(code),
            RecoveryCode.used_at.is_(None),
        )
        .values(used_at=datetime.now(UTC))
        .returning(RecoveryCode.id)
    )
    return used is not None


class Enrolment(BaseModel):
    secret: str
    otpauth_uri: str


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class RecoveryCodes(BaseModel):
    recovery_codes: list[str]


class DisableIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)
    code: str = Field(min_length=6, max_length=32)


def _secret_key(request: Request) -> str:
    key: str = request.app.state.totp_key
    return key


@router.post("/enrol", response_model=Enrolment)
async def enrol(user: CurrentUser, request: Request, db: Db) -> Enrolment:
    """Start (or restart) setup; nothing changes for sign-in until a code is verified."""
    if await is_enabled(db, user.id):
        raise HTTPException(status.HTTP_409_CONFLICT, ALREADY_ON)
    secret = pyotp.random_base32()
    blob = encrypt_secret(_secret_key(request), user.id, secret)
    await db.execute(delete(Totp).where(Totp.user_id == user.id))
    db.add(Totp(user_id=user.id, secret_ciphertext=blob))
    await db.commit()
    uri = TOTP(secret).provisioning_uri(name=user.email, issuer_name=ISSUER)
    return Enrolment(secret=secret, otpauth_uri=uri)


@router.post("/verify", response_model=RecoveryCodes)
async def verify(
    body: CodeIn, user: CurrentUser, request: Request, response: Response, db: Db
) -> RecoveryCodes:
    """Turn 2FA on with a first valid code; the recovery codes are shown only here."""
    await check_code_rate(request.app.state.redis, user.id)
    row = await db.scalar(select(Totp).where(Totp.user_id == user.id))
    if row is None:
        raise HTTPException(status.HTTP_409_CONFLICT, NOT_STARTED)
    if row.enabled_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, ALREADY_ON)
    secret = decrypt_secret(_secret_key(request), user.id, row.secret_ciphertext)
    step = matching_step(secret, _normalise(body.code))
    if step is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, BAD_CODE)
    row.enabled_at = datetime.now(UTC)
    row.last_counter = step
    codes = new_recovery_codes()
    await db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    await db.execute(
        insert(RecoveryCode),
        [{"user_id": user.id, "code_hash": _recovery_hash(code)} for code in codes],
    )
    # Owner and admin gain control rights here, so the session id changes with them.
    await revoke(db, request.state.session_id)
    ip = request.client.host if request.client else None
    raw = await new_session(db, user.id, ip, request.headers.get("user-agent"), stepped_up=True)
    await db.commit()
    set_session_cookie(response, raw)
    return RecoveryCodes(recovery_codes=codes)


@router.post("/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable(body: DisableIn, user: CurrentUser, request: Request, db: Db) -> None:
    await check_code_rate(request.app.state.redis, user.id)
    password_ok = await run_in_threadpool(verify_password, user.password_hash, body.password)
    code_ok = await verify_code(db, _secret_key(request), user.id, body.code)
    if not (password_ok and code_ok):
        await db.rollback()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, DISABLE_REFUSED)
    await db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    await db.execute(delete(Totp).where(Totp.user_id == user.id))
    await db.commit()
