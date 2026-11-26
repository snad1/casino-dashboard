"""The services the signed-in user may open, for the top-bar switcher."""

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import registry
from app.auth.models import Role, User, UserRole
from app.auth.sessions import current_user
from app.db.engine import get_session
from app.db.models import Service
from app.live.aggregator import STALE_AFTER_S
from app.live.pipeline import LivePipeline

router = APIRouter(tags=["services"])

# A global role of these opens every service; a lane manager or a Telegram user never holds one.
_EVERY_SERVICE = frozenset({Role.OWNER, Role.ADMIN, Role.OPERATOR, Role.VIEWER})


class ServiceOut(BaseModel):
    name: str
    label: str
    ctl: str
    installed: bool | None
    live: bool


def _live(pipeline: LivePipeline | None, now_ms: float) -> bool:
    last = pipeline.consumer.last_event_ms if pipeline is not None else None
    return last is not None and now_ms - last <= STALE_AFTER_S * 1000


@router.get("/services")
async def services(
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[ServiceOut]:
    """The enabled services the user holds a role on, in `DASHBOARD_SERVICES` order."""
    if user.disabled:
        return []
    rows = await db.execute(
        select(UserRole.role, Service.name)
        .outerjoin(Service, Service.id == UserRole.service_id)
        .where(UserRole.user_id == user.id)
    )
    every = False
    named: set[str] = set()
    for role, service in rows:
        if service is None:
            every = every or Role(role) in _EVERY_SERVICE
        elif Role(role) != Role.TG_USER:
            named.add(service)
    pipelines = {p.svc: p for p in request.app.state.pipelines}
    now_ms = time.time() * 1000
    out = []
    for name in registry.enabled():
        if not (every or name in named):
            continue
        entry, pipeline = registry.KNOWN[name], pipelines.get(name)
        out.append(
            ServiceOut(
                name=name,
                label=entry.label,
                ctl=entry.ctl,
                installed=pipeline.resync.installed if pipeline is not None else None,
                live=_live(pipeline, now_ms),
            )
        )
    return out
