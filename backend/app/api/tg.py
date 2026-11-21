"""One Telegram user's bot data: their history, exports, live status, settings and controls.

Every `/tg/{uid}/...` route goes through `tg_access`: a Telegram account reaches its own uid
only, and whoever manages users reaches any. Another uid, existing or not, answers the same
403. History is the dashboard's copy of that user's databases (`db_epochs.tg_uid`); status,
settings and controls go to the bot through the host agent (`wolfbet_tg.*`), always with the
uid from the path, never one from the body. Controls are idempotent and audited like the
daemon's.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

import structlog
from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_client import AgentError
from app.api.analytics import csv_text
from app.api.control import (
    AGENT_DOWN,
    BUSY,
    IN_PROGRESS,
    KEY_PATTERN,
    KEY_REUSED,
    _audit,
    _claim,
    _fingerprint,
    _finish,
    _release,
    agent_or_error,
)
from app.api.history import (
    CURSOR,
    BetOut,
    BetPage,
    Resolution,
    SeriesOut,
    SessionDetail,
    SessionOut,
    SessionPage,
    _session_out,
    bet_out,
    bet_page,
    find_session,
    series,
    session_page,
)
from app.auth.models import User
from app.auth.permissions import Perm, TgUid, require, tg_access
from app.db.engine import get_session
from app.history.models import Bet, DbEpoch, HistorySession

SERVICE = "wolfbet-tg"
CSV_MAX_ROWS = 100_000
NO_SESSION = "No session is running there."
REFUSED = "The bot could not do that. Check the bot in Telegram."
TOO_SLOW = "The bot did not answer in time. Try again."

log = structlog.get_logger("app.tg")
router = APIRouter(prefix="/tg", tags=["telegram"])
Db = Annotated[AsyncSession, Depends(get_session)]
Owner = Annotated[User, Depends(tg_access)]
Manager = Annotated[User, Depends(require(Perm.USERS_MANAGE))]
EpochId = Annotated[int, Path(ge=1)]
SessionId = Annotated[int, Path(ge=1)]


class TgUserOut(BaseModel):
    uid: int
    sessions: int
    bets: int
    profit: float
    last_session_at: datetime | None


class TgUserList(BaseModel):
    items: list[TgUserOut]


class TgStatusOut(BaseModel):
    sessions: list[dict[str, Any]]


class TgConfigOut(BaseModel):
    config: dict[str, Any]


class TgControlIn(BaseModel):
    slot: Annotated[int | None, Field(ge=1, le=1000)] = None


class TgControlOut(BaseModel):
    slots: list[int]
    session_id: int | None = None


def user_epochs(uid: int) -> Select[int]:
    """The ids of the databases ingest copied for `uid`."""
    return select(DbEpoch.epoch_id).where(DbEpoch.service == SERVICE, DbEpoch.tg_uid == uid)


def _csv_response(model: type[BaseModel], rows: list[BaseModel], name: str) -> Response:
    return Response(
        csv_text(model, rows),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/users", response_model=TgUserList)
async def list_tg_users(_: Manager, db: Db) -> TgUserList:
    """Every Telegram user ingest has seen, with their totals, newest activity first."""
    query = (
        select(
            DbEpoch.tg_uid,
            func.count(HistorySession.src_id),
            func.coalesce(func.sum(HistorySession.total_bets), 0),
            func.coalesce(func.sum(HistorySession.profit), 0.0),
            func.max(HistorySession.started_at),
        )
        .outerjoin(
            HistorySession,
            (HistorySession.service == DbEpoch.service)
            & (HistorySession.epoch_id == DbEpoch.epoch_id),
        )
        .where(DbEpoch.service == SERVICE, DbEpoch.tg_uid.is_not(None))
        .group_by(DbEpoch.tg_uid)
        .order_by(func.max(HistorySession.started_at).desc().nulls_last(), DbEpoch.tg_uid)
    )
    rows = (await db.execute(query)).all()
    return TgUserList(
        items=[
            TgUserOut(
                uid=uid,
                sessions=n,
                bets=int(bets or 0),
                profit=float(profit or 0),
                last_session_at=at,
            )
            for uid, n, bets, profit, at in rows
        ]
    )


@router.get("/{uid}/sessions", response_model=SessionPage)
async def list_user_sessions(
    uid: TgUid,
    _: Owner,
    db: Db,
    since: datetime | None = None,
    until: datetime | None = None,
    session_status: Annotated[Literal["running", "ended"] | None, Query(alias="status")] = None,
    cursor: Annotated[str | None, Query(pattern=CURSOR)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> SessionPage:
    """The user's sessions, newest first; pass `next_cursor` back as `cursor`."""
    return await session_page(
        db, SERVICE, user_epochs(uid), None, since, until, session_status, cursor, limit
    )


@router.get("/{uid}/sessions.csv", response_class=Response)
async def export_user_sessions(uid: TgUid, _: Owner, db: Db) -> Response:
    """Every session of the user, oldest first, as a CSV download."""
    query = (
        select(HistorySession)
        .where(HistorySession.service == SERVICE, HistorySession.epoch_id.in_(user_epochs(uid)))
        .order_by(HistorySession.epoch_id, HistorySession.src_id)
        .limit(CSV_MAX_ROWS)
    )
    rows: list[BaseModel] = [_session_out(SessionOut, row) for row in await db.scalars(query)]
    return _csv_response(SessionOut, rows, f"wolfbet-tg-{uid}-sessions.csv")


@router.get("/{uid}/sessions/{epoch_id}/{session_id}", response_model=SessionDetail)
async def get_user_session(
    uid: TgUid, epoch_id: EpochId, session_id: SessionId, _: Owner, db: Db
) -> SessionDetail:
    row = await find_session(db, SERVICE, epoch_id, session_id, user_epochs(uid))
    return _session_out(SessionDetail, row)


@router.get("/{uid}/sessions/{epoch_id}/{session_id}/bets", response_model=BetPage)
async def list_user_session_bets(
    uid: TgUid,
    epoch_id: EpochId,
    session_id: SessionId,
    _: Owner,
    db: Db,
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> BetPage:
    """The session's bets, newest first; pass `next_before` back as `before`."""
    await find_session(db, SERVICE, epoch_id, session_id, user_epochs(uid))
    return await bet_page(db, SERVICE, epoch_id, session_id, before, limit)


@router.get("/{uid}/sessions/{epoch_id}/{session_id}/bets.csv", response_class=Response)
async def export_user_session_bets(
    uid: TgUid, epoch_id: EpochId, session_id: SessionId, _: Owner, db: Db
) -> Response:
    """The session's bets, oldest first, as a CSV download."""
    await find_session(db, SERVICE, epoch_id, session_id, user_epochs(uid))
    query = (
        select(Bet)
        .where(Bet.service == SERVICE, Bet.epoch_id == epoch_id, Bet.session_src_id == session_id)
        .order_by(Bet.src_id)
        .limit(CSV_MAX_ROWS)
    )
    rows: list[BaseModel] = [bet_out(row) for row in await db.scalars(query)]
    return _csv_response(BetOut, rows, f"wolfbet-tg-{uid}-session-{session_id}-bets.csv")


@router.get("/{uid}/series", response_model=SeriesOut)
async def get_user_series(
    uid: TgUid,
    _: Owner,
    db: Db,
    metric: Literal["profit", "balance"] = "profit",
    res: Resolution | None = None,
    since: Annotated[datetime | None, Query(alias="from")] = None,
    until: Annotated[datetime | None, Query(alias="to")] = None,
) -> SeriesOut:
    """The user's profit or balance over a range, from their own bets only."""
    epochs: list[int] = list(await db.scalars(user_epochs(uid)))
    return await series(db, SERVICE, epochs, metric, res, since, until, None)


async def _ask[T: BaseModel](request: Request, cmd: str, uid: int, model: type[T]) -> T:
    """The bot's answer to a read for `uid`, as `model`; a malformed answer is the agent's
    failure, never a 500."""
    try:
        data = await agent_or_error(request).call(f"wolfbet_tg.{cmd}", {"uid": uid})
        try:
            return model.model_validate(data)
        except ValidationError:
            raise AgentError("bad_reply") from None
    except AgentError as error:
        raise _agent_refusal(cmd, error) from None


def _agent_refusal(cmd: str, error: AgentError) -> HTTPException:
    log.warning("telegram bot command failed at the agent", cmd=cmd, code=error.code)
    if error.code == "not_found":
        return HTTPException(status.HTTP_404_NOT_FOUND, NO_SESSION)
    if error.code == "bad_request":
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, REFUSED)
    if error.code == "busy":
        return HTTPException(status.HTTP_409_CONFLICT, BUSY)
    if error.code == "timeout":
        return HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, TOO_SLOW)
    return HTTPException(status.HTTP_502_BAD_GATEWAY, AGENT_DOWN)


@router.get("/{uid}/status", response_model=TgStatusOut)
async def get_user_status(uid: TgUid, _: Owner, request: Request) -> TgStatusOut:
    """The user's running sessions as the bot sees them now, one per slot."""
    return await _ask(request, "status", uid, TgStatusOut)


@router.get("/{uid}/config", response_model=TgConfigOut)
async def get_user_config(uid: TgUid, _: Owner, request: Request) -> TgConfigOut:
    """The user's saved bot settings, without the API key or proxy."""
    return await _ask(request, "config", uid, TgConfigOut)


def _control_out(action: str, data: dict[str, Any]) -> TgControlOut:
    """The bot's answer as the route's shape: start names one new slot, the rest a list."""
    try:
        if action == "start":
            return TgControlOut(slots=[data["slot"]], session_id=data.get("session_id"))
        return TgControlOut.model_validate(data)
    except (KeyError, ValidationError):
        raise AgentError("bad_reply") from None


@router.post("/{uid}/control/{action}", response_model=TgControlOut)
async def control_user(
    uid: TgUid,
    action: Literal["start", "stop", "pause", "resume"],
    body: TgControlIn,
    request: Request,
    response: Response,
    user: Owner,
    db: Db,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", pattern=KEY_PATTERN)],
) -> TgControlOut:
    """Start a session, or stop, pause or resume one (a slot is needed when several run)."""
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    fingerprint = _fingerprint(f"{SERVICE}:{uid}", action, body)
    claimed, existing = await _claim(
        sessionmaker, user.id, SERVICE, Perm.CONTROL_BASIC, idempotency_key, fingerprint
    )
    if claimed is None:
        if existing is not None and existing.body_sha256 != fingerprint:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, KEY_REUSED)
        if existing is None or existing.state != "done" or existing.response is None:
            raise HTTPException(status.HTTP_409_CONFLICT, IN_PROGRESS)
        if (existing.status_code or 500) >= 400:
            raise HTTPException(existing.status_code or 500, existing.response.get("detail"))
        return TgControlOut.model_validate(existing.response)
    args: dict[str, Any] = {"uid": uid}
    if body.slot is not None and action != "start":
        args["slot"] = body.slot
    name = f"tg.{action}"
    out: TgControlOut | None = None
    try:
        await _audit(db, request, user, SERVICE, name, args, "requested")
        try:
            data = await agent_or_error(request).call(f"wolfbet_tg.{action}", args)
            out = _control_out(action, data)
            status_code, stored, result = status.HTTP_200_OK, out.model_dump(), "succeeded"
        except AgentError as error:
            refusal = _agent_refusal(action, error)
            status_code, stored = refusal.status_code, {"detail": refusal.detail}
            result = f"agent:{error.code}"
    except BaseException:
        await _release(sessionmaker, claimed)
        raise
    await _finish(sessionmaker, claimed, status_code, stored, None)
    try:
        await _audit(db, request, user, SERVICE, name, args, result)
    except Exception:
        log.exception("audit write failed after a telegram control", action=action)
    if out is None:
        raise HTTPException(status_code, stored["detail"])
    response.status_code = status_code
    return out
