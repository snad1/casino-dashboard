"""Sign-in, sign-out and the current user, under `/auth` (served publicly as `/api/auth`)."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.auth.models import User, UserRole
from app.auth.passwords import dummy_verify, verify_password
from app.auth.ratelimit import check_login_rate
from app.auth.sessions import (
    SESSION_COOKIE,
    clear_session_cookie,
    csrf_token,
    current_user,
    decode_session_id,
    new_session,
    revoke,
    revoke_all,
    set_session_cookie,
)
from app.auth.stepup import NextStepRequired
from app.auth.totp import is_enabled, verify_code
from app.db.engine import get_session
from app.db.models import Service
from app.mail import templates
from app.mail.outbox import enqueue

LOCK_AFTER = 10
LOCK_FOR = timedelta(minutes=15)
BAD_LOGIN = "Email or password is incorrect."
CODE_NEEDED = "Enter the code from your authenticator app."

router = APIRouter(prefix="/auth", tags=["auth"])
Db = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(current_user)]


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    code: str | None = Field(default=None, max_length=32)


class RoleOut(BaseModel):
    service: str | None
    role: str


class Me(BaseModel):
    email: str | None
    tg_uid: int | None
    roles: list[RoleOut]
    csrf_token: str
    totp_enabled: bool


async def _password_ok(user: User, password: str) -> bool:
    matches = await run_in_threadpool(verify_password, user.password_hash, password)
    locked = user.locked_until is not None and user.locked_until > datetime.now(UTC)
    return matches and not locked and not user.disabled


def _record_failure(db: AsyncSession, user: User) -> None:
    if user.locked_until is not None and user.locked_until > datetime.now(UTC):
        return
    user.failed_count += 1
    if user.failed_count >= LOCK_AFTER:
        user.locked_until = datetime.now(UTC) + LOCK_FOR
        user.failed_count = 0
        enqueue(db, user.email, templates.lockout(_stamp(user.locked_until)))


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
async def login(body: LoginIn, request: Request, response: Response, db: Db) -> None:
    ip = request.client.host if request.client else None
    await check_login_rate(request.app.state.redis, body.email, ip)
    user = await db.scalar(select(User).where(User.email == body.email.strip()))
    # Every failure path runs one argon2 verify, so timing does not reveal the reason.
    if user is None:
        await run_in_threadpool(dummy_verify, body.password)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)
    if not await _password_ok(user, body.password):
        _record_failure(db, user)
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)
    two_factor = await is_enabled(db, user.id)
    if two_factor:
        if not body.code:
            raise NextStepRequired(status.HTTP_401_UNAUTHORIZED, CODE_NEEDED, "totp_required")
        if not await verify_code(db, request.app.state.totp_key, user.id, body.code):
            _record_failure(db, user)
            await db.commit()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)
    user.failed_count = 0
    user.locked_until = None
    # A session id planted before sign-in must not survive it.
    if old := decode_session_id(request.cookies.get(SESSION_COOKIE)):
        await revoke(db, old)
    user_agent = request.headers.get("user-agent")
    raw = await new_session(db, user.id, ip, user_agent, stepped_up=two_factor)
    browser = (user_agent or "unknown browser")[:120]
    notice = templates.new_login(_stamp(datetime.now(UTC)), ip or "unknown", browser)
    enqueue(db, user.email, notice)
    await db.commit()
    set_session_cookie(response, raw)
    request.state.audit_actor = user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: Db) -> None:
    if raw := decode_session_id(request.cookies.get(SESSION_COOKIE)):
        await revoke(db, raw)
        await db.commit()
    clear_session_cookie(response)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(user: CurrentUser, response: Response, db: Db) -> None:
    await revoke_all(db, user.id)
    await db.commit()
    clear_session_cookie(response)


@router.get("/me", response_model=Me)
async def me(user: CurrentUser, request: Request, db: Db) -> Me:
    rows = await db.execute(
        select(Service.name, UserRole.role)
        .select_from(UserRole)
        .outerjoin(Service, Service.id == UserRole.service_id)
        .where(UserRole.user_id == user.id)
        .order_by(Service.name.nulls_first())
    )
    secret: str = request.app.state.session_secret
    return Me(
        email=user.email,
        tg_uid=user.tg_uid,
        roles=[RoleOut(service=service, role=role) for service, role in rows],
        csrf_token=csrf_token(secret, request.state.session_id),
        totp_enabled=await is_enabled(db, user.id),
    )
