"""Invites: an owner or admin invites an email with a role; the link sets a password once.

The role may be on every service, one service, or some lanes or vault keys of it (checked
against the bot's lanes and keys when sent and again when accepted), or a lane manager on one
service.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.auth.admin import (
    Keys,
    Lanes,
    check_scope,
    distinct_keys,
    distinct_lanes,
    require_keys,
    require_lanes,
    role_rows,
)
from app.auth.models import Invite, Role, User
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
    role: Literal["admin", "operator", "viewer", "lane_manager"]
    service: str | None = Field(default=None, max_length=100)
    lanes: Lanes
    keys: Keys

    _distinct = field_validator("lanes")(distinct_lanes)
    _distinct_keys = field_validator("keys")(distinct_keys)

    @model_validator(mode="after")
    def _scope(self) -> "InviteIn":
        check_scope(self.role, self.service, self.lanes, self.keys)
        return self

    def scope(self) -> str:
        """The role as the email names it: `viewer on wolfbet lanes 3, 5` or `... key lakai`."""
        where = f" on {self.service}" if self.service else ""
        which = ""
        for noun, items in (("lane", list(map(str, self.lanes))), ("key", self.keys)):
            if items:
                which = f" {noun}{'' if len(items) == 1 else 's'} {', '.join(items)}"
        return f"{self.role}{where}{which}"


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
    if body.service is not None and body.lanes:
        await require_lanes(request, body.service, body.lanes)
    if body.service is not None and body.keys:
        await require_keys(request, body.service, body.keys)
    await db.execute(delete(Invite).where(Invite.email == email, Invite.accepted_at.is_(None)))
    token = secrets.token_urlsafe(32)
    db.add(
        Invite(
            token_hash=token_hash(token),
            email=email,
            role=body.role,
            service_id=service_id,
            lanes=body.lanes,
            key_refs=body.keys,
            invited_by=inviter.id,
            expires_at=datetime.now(UTC) + INVITE_TTL,
        )
    )
    # The token rides in the fragment, so it never reaches a server log or a Referer header.
    url = f"{request.app.state.public_origin}/invite#token={token}"
    enqueue(db, email, templates.invite(url, body.scope(), inviter.email or "an admin"))
    await db.commit()


async def _scope_still_there(request: Request, db: AsyncSession, invite: Invite) -> None:
    """Refuse an invite whose lanes or keys are not all still there, as an invalid link."""
    service = await db.scalar(select(Service.name).where(Service.id == invite.service_id))
    try:
        if invite.lanes:
            await require_lanes(request, service or "", invite.lanes)
        if invite.key_refs:
            await require_keys(request, service or "", invite.key_refs)
    except HTTPException as error:
        if error.status_code != status.HTTP_422_UNPROCESSABLE_CONTENT:
            raise
        raise HTTPException(status.HTTP_400_BAD_REQUEST, INVITE_INVALID) from None


@router.post("/accept", status_code=status.HTTP_204_NO_CONTENT)
async def accept_invite(body: AcceptIn, request: Request, db: Db) -> None:
    invite = await db.scalar(
        select(Invite).where(Invite.token_hash == token_hash(body.token)).with_for_update()
    )
    if invite is None or invite.accepted_at is not None or invite.expires_at <= datetime.now(UTC):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, INVITE_INVALID)
    if await db.scalar(select(User.id).where(User.email == invite.email)) is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, INVITE_INVALID)
    if problem := password_problem(body.password, invite.email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, problem)
    if invite.lanes or invite.key_refs:
        await _scope_still_there(request, db, invite)
    user = User(
        email=invite.email,
        password_hash=await run_in_threadpool(hash_password, body.password),
        created_by=invite.invited_by,
    )
    db.add(user)
    await db.flush()
    db.add_all(
        role_rows(user.id, invite.service_id, Role(invite.role), invite.lanes, invite.key_refs)
    )
    invite.accepted_at = datetime.now(UTC)
    await db.commit()
