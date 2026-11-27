"""Whether a bot's checkout is behind its upstream, and the owner's auto-update switch.

The update itself is the `update` control action: rolling, behind a fresh code. This only
says when it is worth pressing, and lets the owner hand it to a green CI run on main.
"""

import time
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import registry
from app.agent_client import AgentError
from app.api.control import NOT_CONTROLLABLE, ServiceName, agent_or_error
from app.auth.models import Role, User
from app.auth.permissions import FORBIDDEN, Perm, require, roles_for
from app.auth.sessions import current_user
from app.auth.stepup import require_fresh
from app.db.engine import get_session
from app.db.models import Service

router = APIRouter(tags=["updates"])
log = structlog.get_logger("app.updates")

CHECK_EVERY_S = 120.0
Db = Annotated[AsyncSession, Depends(get_session)]


class UpdateOut(BaseModel):
    behind: int | None
    head: str | None
    latest: str | None
    auto_update: bool


class AutoUpdateIn(BaseModel):
    enabled: bool


Check = dict[str, Any]


def _served(service: str) -> None:
    if not registry.is_enabled(service):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_CONTROLLABLE)


def _checks(request: Request) -> dict[str, tuple[float, Check]]:
    checks: dict[str, tuple[float, Check]] = request.app.state.update_checks
    return checks


def forget_check(request: Request, service: str) -> None:
    """Drop the cached check, so the next look asks git again (after an update starts)."""
    _checks(request).pop(service, None)


async def _check(request: Request, service: str) -> Check:
    """The agent's answer, kept for a while; a failed ask is never kept."""
    cached = _checks(request).get(service)
    now = time.monotonic()
    if cached is not None and now - cached[0] < CHECK_EVERY_S:
        return cached[1]
    unknown: Check = {"behind": None, "head": None, "latest": None}
    try:
        data = await agent_or_error(request).call(f"{service}.update_check", {})
    except (AgentError, HTTPException) as error:
        log.warning("update check failed", svc=service, error=getattr(error, "code", "no_agent"))
        return unknown
    check = {key: data.get(key) for key in unknown}
    if check["behind"] is not None:
        _checks(request)[service] = (now, check)
    return check


async def auto_update_on(db: AsyncSession, service: str) -> bool:
    return bool(await db.scalar(select(Service.auto_update).where(Service.name == service)))


@router.get("/{service}/update")
async def update_state(
    service: ServiceName,
    request: Request,
    _: Annotated[User, Depends(require(Perm.VIEW))],
    db: Db,
) -> UpdateOut:
    _served(service)
    check = await _check(request, service)
    return UpdateOut(**check, auto_update=await auto_update_on(db, service))


async def _owner(user: Annotated[User, Depends(current_user)], db: Db) -> User:
    if Role.OWNER not in await roles_for(db, user.id, None):
        raise HTTPException(status.HTTP_403_FORBIDDEN, FORBIDDEN)
    return user


@router.put("/{service}/update/auto")
async def set_auto_update(
    service: ServiceName,
    body: AutoUpdateIn,
    request: Request,
    _owner_user: Annotated[User, Depends(_owner)],
    _fresh: Annotated[User, Depends(require_fresh())],
    db: Db,
) -> UpdateOut:
    _served(service)
    await db.execute(
        update(Service).where(Service.name == service).values(auto_update=body.enabled)
    )
    await db.commit()
    check = await _check(request, service)
    return UpdateOut(**check, auto_update=body.enabled)
