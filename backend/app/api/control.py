"""Control actions on a bot through the host agent, and the jobs they start.

`POST /{service}/control/<action>` needs the action's permission on the service and, for
config writes and destructive actions, a step-up in the last 5 minutes. Every request
carries an `Idempotency-Key`: the first request with a key runs; a retry with the same key
and body within 24 h gets the stored answer and runs nothing; the same key with another
body is refused. Each action is audited before the agent is called (and does not run when
that row can't be written) and again with its outcome. Agent failures reach the user as one
fixed message; the agent's code is only logged.

`GET /{service}/jobs/{job_id}` shows a job started here to anyone who still holds the
permission it was started with; any other id reads as not found.
"""

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, Response, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters.base import ControlAction
from app.adapters.wolfbet.control import ACTIONS as WOLFBET_ACTIONS
from app.adapters.wolfbet.control import ConfigChanges, owner_keys
from app.agent_client import AgentClient, AgentError
from app.api.lane_access import (
    added_lane,
    check_lane_action,
    drop_lane,
    grant_lane,
    may_act_on_a_lane,
    owned_lanes,
)
from app.audit import AuditEntry, record
from app.auth.models import Role, User
from app.auth.permissions import FORBIDDEN, Perm, denial, require, roles_for
from app.auth.sessions import current_user
from app.auth.stepup import require_fresh
from app.db.engine import get_session
from app.db.models import Base
from app.logging import current_request_id

IDEMPOTENCY_TTL = timedelta(hours=24)
RUNNING_LOOKBACK = timedelta(hours=1)
RUNNING_CHECKS = 5
KEY_PATTERN = r"^[A-Za-z0-9_-]{16,128}$"
JOB_ID_PATTERN = r"^[0-9a-f]{32}$"
BUSY = "Another control action is running. Try again when it finishes."
AGENT_DOWN = "The bot host did not respond. Try again."
KEY_REUSED = "This request was already sent with different settings. Reload and try again."
IN_PROGRESS = "This action is still being sent. Wait for it to finish."
NOT_CONTROLLABLE = "That action is not available for this service."
JOB_NOT_FOUND = "Job not found."
# Terminal escapes and control characters; newlines and tabs stay.
_ESCAPES = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]|[\x00-\x08\x0b-\x1f\x7f]"
)

CONTROLS: dict[str, dict[str, ControlAction]] = {"wolfbet": WOLFBET_ACTIONS}

log = structlog.get_logger("app.control")
router = APIRouter(tags=["control"])
Db = Annotated[AsyncSession, Depends(get_session)]
ServiceName = Annotated[str, Path(max_length=100)]


class ControlRequest(Base):
    """One control request per user and idempotency key, with the answer it got."""

    __tablename__ = "control_requests"
    __table_args__ = (
        UniqueConstraint("user_id", "idem_key"),
        CheckConstraint("state IN ('pending', 'done')", name="state"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    service: Mapped[str] = mapped_column(Text)
    idem_key: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    perm: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, server_default="pending")
    status_code: Mapped[int | None] = mapped_column(Integer)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    job_id: Mapped[str | None] = mapped_column(Text, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ControlOut(BaseModel):
    status: Literal["succeeded", "failed", "running"]
    exit_code: int | None
    job_id: str | None
    output: str | None = None


class JobOut(BaseModel):
    job_id: str
    cmd: str
    status: Literal["running", "succeeded", "failed", "cancelled", "timeout"]
    exit_code: int | None
    duration_ms: int
    lines: int


class ActionOut(BaseModel):
    name: str
    label: str
    title: str
    effect: str
    confirm: str
    perm: str
    typed_confirm: bool
    confirm_first: bool
    job: bool


class ControlMenu(BaseModel):
    actions: list[ActionOut]
    running_job: JobOut | None


def _fingerprint(service: str, action: str, body: BaseModel) -> bytes:
    canonical = json.dumps(
        {"service": service, "action": action, "body": body.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).digest()


def agent_args(spec: ControlAction, body: BaseModel) -> dict[str, Any]:
    """What the agent is sent for `body`: the action's own mapping, or the body's fields."""
    if spec.agent_args is not None:
        return spec.agent_args(body)
    return body.model_dump(mode="json")


def agent_or_error(request: Request) -> AgentClient:
    agent: AgentClient | None = request.app.state.agent
    if agent is None:
        raise AgentError("unconfigured")
    return agent


async def job_access(db: AsyncSession, user: User, service: str, job_id: str) -> bool:
    """True when `job_id` was started here on `service` with a permission `user` still has,
    or by `user` themself while they still hold a lane role on `service`."""
    row = (
        await db.execute(
            select(ControlRequest.perm, ControlRequest.user_id).where(
                ControlRequest.service == service, ControlRequest.job_id == job_id
            )
        )
    ).first()
    if row is None:
        return False
    if await denial(db, user, service, Perm(row.perm)) is None:
        return True
    return (
        row.user_id == user.id
        and not user.disabled
        and bool(await owned_lanes(db, user.id, service))
    )


async def _claim(
    sessionmaker: async_sessionmaker[AsyncSession],
    user_id: int,
    service: str,
    perm: Perm,
    key: str,
    fingerprint: bytes,
) -> tuple[int | None, ControlRequest | None]:
    """(id of the new pending row, None), or (None, the row an earlier request left)."""
    async with sessionmaker() as db, db.begin():
        await db.execute(
            delete(ControlRequest).where(
                ControlRequest.created_at < datetime.now(UTC) - IDEMPOTENCY_TTL
            )
        )
        claimed = await db.scalar(
            insert(ControlRequest)
            .values(
                user_id=user_id,
                service=service,
                idem_key=key,
                body_sha256=fingerprint,
                perm=perm.value,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "idem_key"])
            .returning(ControlRequest.id)
        )
        if claimed is not None:
            return claimed, None
        existing = await db.scalar(
            select(ControlRequest).where(
                ControlRequest.user_id == user_id, ControlRequest.idem_key == key
            )
        )
        return None, existing


async def _finish(
    sessionmaker: async_sessionmaker[AsyncSession],
    row_id: int,
    status_code: int,
    stored: dict[str, Any],
    job_id: str | None,
) -> None:
    async with sessionmaker() as db, db.begin():
        await db.execute(
            update(ControlRequest)
            .where(ControlRequest.id == row_id)
            .values(state="done", status_code=status_code, response=stored, job_id=job_id)
        )


async def _release(sessionmaker: async_sessionmaker[AsyncSession], row_id: int) -> None:
    """Forget a claim whose action never reached the agent, so the key can be retried."""
    async with sessionmaker() as db, db.begin():
        await db.execute(delete(ControlRequest).where(ControlRequest.id == row_id))


def _replay(existing: ControlRequest, fingerprint: bytes, response: Response) -> ControlOut:
    if existing.body_sha256 != fingerprint:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, KEY_REUSED)
    if existing.state != "done" or existing.status_code is None or existing.response is None:
        raise HTTPException(status.HTTP_409_CONFLICT, IN_PROGRESS)
    if existing.status_code >= 400:
        raise HTTPException(existing.status_code, existing.response.get("detail"))
    response.status_code = existing.status_code
    return ControlOut.model_validate(existing.response)


def _exit_code(data: dict[str, Any]) -> int:
    """The command's exit code; a config write answers with its dry run and its set step."""
    for part in (data, data.get("set"), data.get("dry_run")):
        if isinstance(part, dict) and isinstance(part.get("exit_code"), int):
            code: int = part["exit_code"]
            return code
    raise AgentError("bad_reply")


async def _run(
    agent: AgentClient, service: str, spec: ControlAction, args: dict[str, Any]
) -> tuple[int, ControlOut]:
    cmd = f"{service}.{spec.cmd}"
    if spec.job:
        data = await agent.call("job.start", {"cmd": cmd, "args": args})
        job_id = data.get("job_id")
        if not isinstance(job_id, str) or not re.fullmatch(JOB_ID_PATTERN, job_id):
            raise AgentError("bad_reply")
        return status.HTTP_202_ACCEPTED, ControlOut(status="running", exit_code=None, job_id=job_id)
    data = await agent.call(cmd, args)
    code = _exit_code(data)
    outcome: Literal["succeeded", "failed"] = "succeeded" if code == 0 else "failed"
    stdout = data.get("stdout")
    output = _ESCAPES.sub("", stdout) if isinstance(stdout, str) else None
    return status.HTTP_200_OK, ControlOut(
        status=outcome, exit_code=code, job_id=None, output=output
    )


async def _audit(
    db: AsyncSession,
    request: Request,
    user: User,
    service: str,
    action: str,
    args: dict[str, Any],
    result: str,
) -> None:
    client = request.client
    entry = AuditEntry(
        action=f"control.{action}",
        result=result,
        actor_id=user.id,
        actor_email=user.email,
        roles=",".join(sorted(await roles_for(db, user.id, service))) or None,
        service=service,
        args=args,
        ip=client.host if client else None,
        user_agent=request.headers.get("user-agent"),
        request_id=current_request_id(),
    )
    await record(request.app.state.sessionmaker, entry)


def _guard(name: str, spec: ControlAction) -> Callable[..., Awaitable[User]]:
    """The user, when they hold the action's permission (and a fresh step-up if it needs one).

    An action on one lane (or a lane add or config change) goes on to the endpoint for a user
    without the service-wide permission, which then checks the lane in the body.
    """
    fresh = require_fresh()

    async def dependency(
        request: Request,
        service: ServiceName,
        user: Annotated[User, Depends(current_user)],
        db: Db,
    ) -> User:
        detail = await denial(db, user, service, spec.perm)
        request.state.lane_refused = detail
        if detail is not None and not await may_act_on_a_lane(db, user, service, name, spec):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail)
        if CONTROLS.get(service, {}).get(name) is not spec:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_CONTROLLABLE)
        if spec.fresh:
            await fresh(user, request)
        return user

    return dependency


def _endpoint(name: str, spec: ControlAction) -> Callable[..., Awaitable[ControlOut]]:
    body_model = spec.body

    async def control(
        service: ServiceName,
        body: body_model,  # type: ignore[valid-type]  # each route is built with its action's model
        request: Request,
        response: Response,
        user: Annotated[User, Depends(_guard(name, spec))],
        db: Db,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", pattern=KEY_PATTERN)],
    ) -> ControlOut:
        payload: BaseModel = body
        refused: str | None = request.state.lane_refused
        if refused is not None:
            await check_lane_action(
                db, agent_or_error(request), user, service, name, spec, payload, refused
            )
        if isinstance(payload, ConfigChanges) and (owned := owner_keys(payload)):
            if Role.OWNER not in await roles_for(db, user.id, None):
                detail = f"Only the owner can change {', '.join(owned)}."
                raise HTTPException(status.HTTP_403_FORBIDDEN, detail)
        sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
        fingerprint = _fingerprint(service, name, payload)
        claimed, existing = await _claim(
            sessionmaker, user.id, service, spec.perm, idempotency_key, fingerprint
        )
        if claimed is None:
            if existing is None:
                raise HTTPException(status.HTTP_409_CONFLICT, IN_PROGRESS)
            return _replay(existing, fingerprint, response)
        args = {**agent_args(spec, payload), **spec.fixed_args}
        out: ControlOut | None = None
        try:
            await _audit(db, request, user, service, name, args, "requested")
            try:
                status_code, out = await _run(agent_or_error(request), service, spec, args)
                stored = out.model_dump()
                result = f"job:{out.job_id}" if out.job_id else out.status
            except AgentError as error:
                log.warning("control action failed at the agent", action=name, code=error.code)
                busy = error.code == "busy"
                status_code = status.HTTP_409_CONFLICT if busy else status.HTTP_502_BAD_GATEWAY
                stored = {"detail": BUSY if busy else AGENT_DOWN}
                result = f"agent:{error.code}"
        except BaseException:
            await _release(sessionmaker, claimed)
            raise
        await _finish(sessionmaker, claimed, status_code, stored, out.job_id if out else None)
        try:
            await _audit(db, request, user, service, name, args, result)
        except Exception:
            log.exception("audit write failed after a control action", action=name)
        if out is None:
            raise HTTPException(status_code, stored["detail"])
        if out.status == "succeeded":
            await _after_lane_change(db, user, service, name, payload, out, refused)
        response.status_code = status_code
        return out

    control.__name__ = f"control_{name}"
    return control


async def _after_lane_change(
    db: AsyncSession,
    user: User,
    service: str,
    name: str,
    body: BaseModel,
    out: ControlOut,
    refused: str | None,
) -> None:
    """A lane added by a lane manager becomes theirs; a removed lane takes its roles along."""
    removed = getattr(body, "lane", None)
    if name == "lane_remove" and isinstance(removed, int):
        await drop_lane(db, service, removed)
    elif name == "lane_add" and refused is not None:
        lane = added_lane(out.output)
        if lane is None:
            log.warning("could not read the new lane's id; no lane role granted")
            return
        await grant_lane(db, user.id, service, lane)


for _name, _spec in WOLFBET_ACTIONS.items():
    router.add_api_route(
        f"/{{service}}/control/{_name}",
        _endpoint(_name, _spec),
        methods=["POST"],
        response_model=ControlOut,
        status_code=status.HTTP_200_OK,
        responses={status.HTTP_202_ACCEPTED: {"model": ControlOut}},
    )


async def _running_job(
    request: Request, db: AsyncSession, user: User, service: str
) -> JobOut | None:
    """The newest job started here that is still running and that `user` may see, if any.

    Only exclusive jobs count: a log follow or a backtest never holds up the buttons. The
    agent forgets finished jobs after an hour, so only that hour is checked. An agent that
    can't answer leaves it unknown (None); the page still loads.
    """
    agent: AgentClient | None = request.app.state.agent
    if agent is None:
        return None
    specs = CONTROLS[service].values()
    exclusive = {f"{service}.{spec.cmd}" for spec in specs if spec.exclusive}
    exclusive_perms = {spec.perm.value for spec in specs if spec.exclusive and spec.job}
    rows = await db.scalars(
        select(ControlRequest)
        .where(
            ControlRequest.service == service,
            ControlRequest.job_id.is_not(None),
            ControlRequest.perm.in_(exclusive_perms),
            ControlRequest.created_at >= datetime.now(UTC) - RUNNING_LOOKBACK,
        )
        .order_by(ControlRequest.id.desc())
        .limit(RUNNING_CHECKS)
    )
    for row in rows:
        if await denial(db, user, service, Perm(row.perm)) is not None:
            continue
        try:
            job = JobOut.model_validate(await agent.call("job.get", {"job_id": row.job_id}))
        except AgentError as error:
            if error.code == "not_found":
                continue
            log.warning("running job lookup failed at the agent", code=error.code)
            return None
        except ValidationError:
            log.warning("running job lookup got an unexpected reply")
            return None
        if job.status == "running" and job.cmd in exclusive:
            return job
    return None


@router.get("/{service}/control", response_model=ControlMenu)
async def control_menu(
    service: ServiceName,
    request: Request,
    user: Annotated[User, Depends(require(Perm.VIEW))],
    db: Db,
) -> ControlMenu:
    """The service's control buttons with their confirm text, and the job still running."""
    actions = CONTROLS.get(service)
    if actions is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_CONTROLLABLE)
    return ControlMenu(
        actions=[
            ActionOut(
                name=name,
                label=spec.label,
                title=spec.title,
                effect=spec.effect,
                confirm=spec.confirm,
                perm=spec.perm.value,
                typed_confirm=spec.perm == Perm.CONTROL_DESTRUCTIVE,
                confirm_first=spec.confirm_first,
                job=spec.job,
            )
            for name, spec in actions.items()
        ],
        running_job=await _running_job(request, db, user, service),
    )


JobId = Annotated[str, Path(pattern=JOB_ID_PATTERN)]


async def job_reader(
    service: ServiceName,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """The caller, when they may view `service` or hold a lane role on it; anyone else is
    refused before the job id is read, so the answer reveals nothing about the route."""
    if await denial(db, user, service, Perm.VIEW) is None:
        return user
    if not user.disabled and await owned_lanes(db, user.id, service):
        return user
    raise HTTPException(status.HTTP_403_FORBIDDEN, FORBIDDEN)


async def job_call(
    request: Request, db: AsyncSession, user: User, service: str, job_id: str, cmd: str
) -> dict[str, Any]:
    """Run agent `cmd` on a job `user` may see; a job they may not see reads as not found."""
    if not await job_access(db, user, service, job_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, JOB_NOT_FOUND)
    try:
        return await agent_or_error(request).call(cmd, {"job_id": job_id})
    except AgentError as error:
        if error.code == "not_found":
            raise HTTPException(status.HTTP_404_NOT_FOUND, JOB_NOT_FOUND) from None
        log.warning("job call failed at the agent", cmd=cmd, code=error.code)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, AGENT_DOWN) from None


def _job_out(data: dict[str, Any]) -> JobOut:
    try:
        return JobOut.model_validate(data)
    except ValidationError:
        log.warning("job reply did not match the schema")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, AGENT_DOWN) from None


@router.get("/{service}/jobs/{job_id}", response_model=JobOut)
async def get_job(
    service: ServiceName,
    job_id: JobId,
    request: Request,
    user: Annotated[User, Depends(job_reader)],
    db: Db,
) -> JobOut:
    """A job's status and output line count, for whoever may still run what started it."""
    return _job_out(await job_call(request, db, user, service, job_id, "job.get"))


@router.post("/{service}/jobs/{job_id}/cancel", response_model=JobOut)
async def cancel_job(
    service: ServiceName,
    job_id: JobId,
    request: Request,
    user: Annotated[User, Depends(job_reader)],
    db: Db,
) -> JobOut:
    """Stop a running job (a log follow, say); a finished job is returned as it is."""
    return _job_out(await job_call(request, db, user, service, job_id, "job.cancel"))
