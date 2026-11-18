"""Invites: an owner or admin invites an email with a role; the link sets a password once."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.auth.models import Invite, Role, User, UserRole
from app.auth.passwords import hash_password, password_problem
from app.auth.permissions import Perm, require
from app.db.engine import get_session
from app.db.models import Service
from app.mail import templates
from app.mail.outbox import enqueue

INVITE_TTL = timedelta(hours=48)
INVITE_INVALID = "This invite link is invalid or has expired."
EMAIL_TAKEN = "That email already has an account."
UNKNOWN_SERVICE = "That service does not exist."

router = APIRouter(prefix="/auth/invites", tags=["auth"])
Db = Annotated[AsyncSession, Depends(get_session)]


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


class InviteIn(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: Literal["admin", "operator", "viewer"]
    service: str | None = Field(default=None, max_length=100)


class AcceptIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=1, max_length=1024)


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def create_invite(
    body: InviteIn,
    request: Request,
    inviter: Annotated[User, Depends(require(Perm.USERS_MANAGE))],
    db: Db,
) -> None:
    """Email a single-use link. Owners are never invited: there is only one, made by the CLI."""
    email = body.email.strip()
    if await db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, EMAIL_TAKEN)
    service_id = None
    if body.service is not None:
        service_id = await db.scalar(select(Service.id).where(Service.name == body.service))
        if service_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, UNKNOWN_SERVICE)
    await db.execute(delete(Invite).where(Invite.email == email, Invite.accepted_at.is_(None)))
    token = secrets.token_urlsafe(32)
    db.add(
        Invite(
            token_hash=token_hash(token),
            email=email,
            role=body.role,
            service_id=service_id,
            invited_by=inviter.id,
            expires_at=datetime.now(UTC) + INVITE_TTL,
        )
    )
    # The token rides in the fragment, so it never reaches a server log or a Referer header.
    url = f"{request.app.state.public_origin}/invite#token={token}"
    enqueue(db, email, templates.invite(url, body.role, inviter.email))
    await db.commit()


@router.post("/accept", status_code=status.HTTP_204_NO_CONTENT)
async def accept_invite(body: AcceptIn, db: Db) -> None:
    invite = await db.scalar(
        select(Invite).where(Invite.token_hash == token_hash(body.token)).with_for_update()
    )
    if invite is None or invite.accepted_at is not None or invite.expires_at <= datetime.now(UTC):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, INVITE_INVALID)
    if await db.scalar(select(User.id).where(User.email == invite.email)) is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, INVITE_INVALID)
    if problem := password_problem(body.password, invite.email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, problem)
    user = User(
        email=invite.email,
        password_hash=await run_in_threadpool(hash_password, body.password),
        created_by=invite.invited_by,
    )
    db.add(user)
    await db.flush()
    db.add(UserRole(user_id=user.id, service_id=invite.service_id, role=Role(invite.role)))
    invite.accepted_at = datetime.now(UTC)
    await db.commit()
