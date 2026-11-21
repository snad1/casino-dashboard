"""Comments on a session or a bet, and notes pinned to a time on the chart.

The text is plain: it is stored as sent (trimmed) and never parsed, and the frontend shows
it as text. Anyone with `view` reads; `comment` writes, at most 10 writes a minute. Only the
author edits a comment; the author or a moderator deletes it. Each write is recorded by the
audit middleware and announced on `svc:<name>:comments` without its text, so open pages
reload what they show and each viewer still gets their own edit rights.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.models import User
from app.auth.permissions import Perm, denial, require
from app.auth.ratelimit import check_comment_rate
from app.db.engine import get_session
from app.db.models import Base, Service
from app.history.models import Bet, HistorySession

MAX_BODY = 2000
MAX_LIST = 500
CHART_RANGE = timedelta(days=31)
TOO_LONG = f"Comments can be at most {MAX_BODY} characters."
EMPTY = "Write something first."
NO_TARGET = "Pick a session, a bet or a time on the chart."
RANGE_INVALID = "Pick a chart range of at most 31 days, with the start before the end."
TARGET_NOT_FOUND = "That session or bet does not exist."
COMMENT_NOT_FOUND = "Comment not found."
TELEGRAM_AUTHOR = "Telegram user"
NOT_YOURS = "You can only change your own comments."

Kind = Literal["session", "bet", "chart"]

router = APIRouter(prefix="/{service}/comments", tags=["comments"])
Db = Annotated[AsyncSession, Depends(get_session)]
Viewer = Annotated[User, Depends(require(Perm.VIEW))]
Commenter = Annotated[User, Depends(require(Perm.COMMENT))]
CommentId = Annotated[int, Path(ge=1)]


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (
        CheckConstraint("kind IN ('session', 'bet', 'chart')", name="kind"),
        CheckConstraint(f"char_length(body) BETWEEN 1 AND {MAX_BODY}", name="body_length"),
        CheckConstraint(
            "(kind = 'session' AND epoch_id IS NOT NULL AND session_src_id IS NOT NULL"
            " AND bet_src_id IS NULL AND at IS NULL)"
            " OR (kind = 'bet' AND epoch_id IS NOT NULL AND bet_src_id IS NOT NULL"
            " AND session_src_id IS NULL AND at IS NULL)"
            " OR (kind = 'chart' AND at IS NOT NULL AND epoch_id IS NULL"
            " AND session_src_id IS NULL AND bet_src_id IS NULL)",
            name="target",
        ),
        Index("ix_comments_session", "service", "kind", "epoch_id", "session_src_id"),
        Index("ix_comments_bet", "service", "kind", "epoch_id", "bet_src_id"),
        Index("ix_comments_at", "service", "at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    service: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    epoch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("db_epochs.epoch_id"))
    session_src_id: Mapped[int | None] = mapped_column(BigInteger)
    bet_src_id: Mapped[int | None] = mapped_column(BigInteger)
    at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    author_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CommentIn(BaseModel):
    kind: Kind
    epoch_id: int | None = Field(default=None, ge=1)
    session_id: int | None = Field(default=None, ge=0)
    bet_id: int | None = Field(default=None, ge=0)
    at: datetime | None = None
    body: str = Field(max_length=4 * MAX_BODY)


class CommentEdit(BaseModel):
    body: str = Field(max_length=4 * MAX_BODY)


class CommentOut(BaseModel):
    id: int
    kind: Kind
    epoch_id: int | None
    session_id: int | None
    bet_id: int | None
    at: datetime | None
    body: str
    author: str
    created_at: datetime
    edited_at: datetime | None
    can_edit: bool
    can_delete: bool


class CommentList(BaseModel):
    items: list[CommentOut]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _clean(body: str) -> str:
    text = body.strip()
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, EMPTY)
    if len(text) > MAX_BODY:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, TOO_LONG)
    return text


def _out(comment: Comment, email: str, user: User, moderator: bool) -> CommentOut:
    mine = comment.author_id == user.id
    return CommentOut(
        id=comment.id,
        kind=comment.kind,
        epoch_id=comment.epoch_id,
        session_id=comment.session_src_id,
        bet_id=comment.bet_src_id,
        at=comment.at,
        body=comment.body,
        author=email.split("@", 1)[0],
        created_at=comment.created_at,
        edited_at=comment.edited_at,
        can_edit=mine,
        can_delete=mine or moderator,
    )


async def _moderator(db: AsyncSession, user: User, service: str) -> bool:
    return await denial(db, user, service, Perm.COMMENTS_MODERATE) is None


def _announce(request: Request, service: str, comment: Comment) -> None:
    hub = getattr(request.app.state, "hub", None)
    if hub is None or hub.svc != service:
        return
    hub.publish_comment(
        {
            "kind": comment.kind,
            "epoch_id": comment.epoch_id,
            "session_id": comment.session_src_id,
            "bet_id": comment.bet_src_id,
            "at": comment.at.isoformat() if comment.at else None,
        }
    )


async def _target_exists(db: AsyncSession, service: str, body: CommentIn) -> bool:
    if body.kind == "session":
        query: Any = select(HistorySession.src_id).where(
            HistorySession.service == service,
            HistorySession.epoch_id == body.epoch_id,
            HistorySession.src_id == body.session_id,
        )
    elif body.kind == "bet":
        query = select(Bet.src_id).where(
            Bet.service == service, Bet.epoch_id == body.epoch_id, Bet.src_id == body.bet_id
        )
    else:
        query = select(Service.id).where(Service.name == service)
    return await db.scalar(query.limit(1)) is not None


def _target_columns(body: CommentIn) -> dict[str, Any]:
    if body.kind == "session" and body.epoch_id is not None and body.session_id is not None:
        return {"epoch_id": body.epoch_id, "session_src_id": body.session_id}
    if body.kind == "bet" and body.epoch_id is not None and body.bet_id is not None:
        return {"epoch_id": body.epoch_id, "bet_src_id": body.bet_id}
    if body.kind == "chart" and body.at is not None:
        return {"at": _utc(body.at)}
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, NO_TARGET)


async def _load(db: AsyncSession, service: str, comment_id: int) -> tuple[Comment, str]:
    row = (
        await db.execute(
            select(Comment, func.coalesce(User.email, TELEGRAM_AUTHOR))
            .join(User, User.id == Comment.author_id)
            .where(Comment.id == comment_id, Comment.service == service)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, COMMENT_NOT_FOUND)
    return row[0], row[1]


@router.get("", response_model=CommentList)
async def list_comments(
    service: str,
    user: Viewer,
    db: Db,
    kind: Kind,
    epoch_id: Annotated[int | None, Query(ge=1)] = None,
    session_id: Annotated[int | None, Query(ge=0)] = None,
    bet_id: Annotated[int | None, Query(ge=0)] = None,
    since: Annotated[datetime | None, Query(alias="from")] = None,
    until: Annotated[datetime | None, Query(alias="to")] = None,
) -> CommentList:
    """One target's comments, oldest first; chart notes in a range, by their time."""
    query = (
        select(Comment, func.coalesce(User.email, TELEGRAM_AUTHOR))
        .join(User, User.id == Comment.author_id)
        .where(Comment.service == service, Comment.kind == kind)
    )
    if kind == "session" and epoch_id is not None and session_id is not None:
        query = query.where(Comment.epoch_id == epoch_id, Comment.session_src_id == session_id)
        query = query.order_by(Comment.id)
    elif kind == "bet" and epoch_id is not None and bet_id is not None:
        query = query.where(Comment.epoch_id == epoch_id, Comment.bet_src_id == bet_id)
        query = query.order_by(Comment.id)
    elif kind == "chart" and since is not None and until is not None:
        start, end = _utc(since), _utc(until)
        if not start < end or end - start > CHART_RANGE:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, RANGE_INVALID)
        query = query.where(Comment.at >= start, Comment.at <= end).order_by(Comment.at, Comment.id)
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, NO_TARGET)
    moderator = await _moderator(db, user, service)
    rows = await db.execute(query.limit(MAX_LIST))
    return CommentList(items=[_out(c, email, user, moderator) for c, email in rows.tuples()])


@router.post("", response_model=CommentOut, status_code=status.HTTP_201_CREATED)
async def create_comment(
    service: str, body: CommentIn, user: Commenter, db: Db, request: Request
) -> CommentOut:
    columns = _target_columns(body)
    text = _clean(body.body)
    await check_comment_rate(request.app.state.redis, user.id)
    if not await _target_exists(db, service, body):
        raise HTTPException(status.HTTP_404_NOT_FOUND, TARGET_NOT_FOUND)
    comment = Comment(service=service, kind=body.kind, author_id=user.id, body=text, **columns)
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    _announce(request, service, comment)
    return _out(comment, user.email or TELEGRAM_AUTHOR, user, await _moderator(db, user, service))


@router.patch("/{comment_id}", response_model=CommentOut)
async def edit_comment(
    service: str,
    comment_id: CommentId,
    body: CommentEdit,
    user: Commenter,
    db: Db,
    request: Request,
) -> CommentOut:
    """Only the author changes the text."""
    text = _clean(body.body)
    comment, email = await _load(db, service, comment_id)
    if comment.author_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_YOURS)
    await check_comment_rate(request.app.state.redis, user.id)
    comment.body = text
    comment.edited_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(comment)
    _announce(request, service, comment)
    return _out(comment, email, user, await _moderator(db, user, service))


@router.delete("/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comment(
    service: str, comment_id: CommentId, user: Commenter, db: Db, request: Request
) -> Response:
    """The author, or a moderator, removes the comment."""
    comment, _ = await _load(db, service, comment_id)
    if comment.author_id != user.id and not await _moderator(db, user, service):
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_YOURS)
    await check_comment_rate(request.app.state.redis, user.id)
    await db.delete(comment)
    await db.commit()
    _announce(request, service, comment)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
