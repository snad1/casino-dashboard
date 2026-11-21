"""Password reset by email. The request answer never tells whether the email has an account."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.auth.invites import token_hash
from app.auth.models import PasswordReset, User
from app.auth.passwords import hash_password, password_problem
from app.auth.ratelimit import check_reset_rate
from app.auth.sessions import revoke_all
from app.db.engine import get_session
from app.mail import templates
from app.mail.outbox import enqueue

RESET_TTL = timedelta(minutes=30)
RESET_SENT = "If that email has an account, a reset link is on its way."
RESET_INVALID = "This reset link is invalid or has expired."

router = APIRouter(prefix="/auth/password-reset", tags=["auth"])
Db = Annotated[AsyncSession, Depends(get_session)]


class ResetIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class ConfirmIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=1, max_length=1024)


class Accepted(BaseModel):
    detail: str


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=Accepted)
async def request_reset(body: ResetIn, request: Request, db: Db) -> Accepted:
    ip = request.client.host if request.client else None
    await check_reset_rate(request.app.state.redis, body.email, ip)
    user = await db.scalar(select(User).where(User.email == body.email.strip()))
    if user is not None and not user.disabled:
        await db.execute(
            delete(PasswordReset).where(
                PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None)
            )
        )
        token = secrets.token_urlsafe(32)
        db.add(
            PasswordReset(
                user_id=user.id,
                token_hash=token_hash(token),
                expires_at=datetime.now(UTC) + RESET_TTL,
            )
        )
        url = f"{request.app.state.public_origin}/reset#token={token}"
        enqueue(db, user.email, templates.password_reset(url))
        await db.commit()
    return Accepted(detail=RESET_SENT)


@router.post("/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_reset(body: ConfirmIn, db: Db) -> None:
    """Set the new password, end every session, and lift any lockout."""
    query = select(PasswordReset).where(PasswordReset.token_hash == token_hash(body.token))
    reset = await db.scalar(query.with_for_update())
    if reset is None or reset.used_at is not None or reset.expires_at <= datetime.now(UTC):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, RESET_INVALID)
    user = await db.get(User, reset.user_id)
    if user is None or user.disabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, RESET_INVALID)
    if problem := password_problem(body.password, user.email or ""):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, problem)
    user.password_hash = await run_in_threadpool(hash_password, body.password)
    user.failed_count = 0
    user.locked_until = None
    reset.used_at = datetime.now(UTC)
    await revoke_all(db, user.id)
    await db.commit()
