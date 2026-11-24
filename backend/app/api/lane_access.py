"""What a user without a service-wide role may do to lanes, and who owns which lane.

A lane manager may create one lane (for now) and gets an operator role on that lane alone;
lane ids are never reused, so that role can never open another lane. Such a user may run a
lane action only on a lane they hold a role on, and change the config only on those lanes,
pointing them only at their own vault keys and proxies. Everything else stays refused.
"""

import re
from typing import Any

import structlog
from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import ControlAction
from app.agent_client import AgentClient, AgentError
from app.auth.models import Role, User, UserRole
from app.auth.permissions import FORBIDDEN, Perm, denial
from app.db.models import Service

LANE_QUOTA = 1
OVER_QUOTA = "You already run 1 lane, the most you may have for now."
NOT_YOUR_KEY = "A lane can only use your own keys."
REF_KINDS = {"key_ref": "key", "proxy_ref": "proxy"}
_ADDED = re.compile(r"\bLane (\d{1,2}) \(")

log = structlog.get_logger("app.lanes")


async def _service_id(db: AsyncSession, service: str) -> int | None:
    found: int | None = await db.scalar(select(Service.id).where(Service.name == service))
    return found


async def owned_lanes(db: AsyncSession, user_id: int, service: str) -> list[int]:
    """The lanes of `service` the user holds a lane role on."""
    rows = await db.scalars(
        select(UserRole.lane)
        .join(Service, Service.id == UserRole.service_id)
        .where(UserRole.user_id == user_id, Service.name == service, UserRole.lane.is_not(None))
    )
    return sorted(lane for lane in rows if lane is not None)


async def grant_lane(db: AsyncSession, user_id: int, service: str, lane: int) -> None:
    service_id = await _service_id(db, service)
    if service_id is None:
        return
    db.add(UserRole(user_id=user_id, service_id=service_id, role=Role.OPERATOR, lane=lane))
    await db.commit()


async def drop_lane(db: AsyncSession, service: str, lane: int) -> None:
    """Remove every role on a lane that no longer exists."""
    service_id = await _service_id(db, service)
    if service_id is None:
        return
    await db.execute(
        delete(UserRole).where(UserRole.service_id == service_id, UserRole.lane == lane)
    )
    await db.commit()


def added_lane(output: str | None) -> int | None:
    """The new lane's id in `wolfctl lane add`'s output, or None when it isn't there."""
    match = _ADDED.search(output or "")
    return int(match.group(1)) if match else None


async def _own_vault_ids(agent: AgentClient, service: str, user_id: int) -> dict[str, str]:
    try:
        data = await agent.call(f"{service}.key_list", {})
    except AgentError as error:
        log.warning("key list failed while checking a lane change", code=error.code)
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_YOUR_KEY) from None
    doc = data.get("json")
    rows = doc.get("keys", []) if isinstance(doc, dict) else []
    return {
        str(row["id"]): str(row.get("kind", "key"))
        for row in rows
        if isinstance(row, dict) and row.get("owner_user_id") == user_id
    }


async def may_act_on_a_lane(
    db: AsyncSession, user: User, service: str, name: str, spec: ControlAction
) -> bool:
    """Whether a user without the service-wide permission could still run `name` on a lane:
    they hold a lane role here, or (for a lane add) may create lanes. Anyone else is refused
    before the body is read, so the answer reveals nothing about the route."""
    if name == "lane_add":
        return await denial(db, user, service, Perm.LANES_CREATE) is None
    if spec.lane_field is None and name != "config_set":
        return False
    return bool(await owned_lanes(db, user.id, service))


async def check_lane_action(
    db: AsyncSession,
    agent: AgentClient,
    user: User,
    service: str,
    name: str,
    spec: ControlAction,
    body: Any,
    refused: str,
) -> None:
    """Allow `name` for a user who lacks the action's service-wide permission, or raise 403."""

    async def lane_ok(lane: object, perm: Perm) -> bool:
        return isinstance(lane, int) and await denial(db, user, service, perm, lane) is None

    if spec.lane_field is not None:
        if not await lane_ok(getattr(body, spec.lane_field), spec.perm):
            raise HTTPException(status.HTTP_403_FORBIDDEN, refused)
        return
    if name == "lane_add":
        if await denial(db, user, service, Perm.LANES_CREATE) is not None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, refused)
        owned = await owned_lanes(db, user.id, service)
        if len(owned) >= LANE_QUOTA:
            raise HTTPException(status.HTTP_403_FORBIDDEN, OVER_QUOTA)
        if body.copy_from is not None and body.copy_from not in owned:
            raise HTTPException(status.HTTP_403_FORBIDDEN, FORBIDDEN)
        return
    if name == "config_set":
        for change in body.changes:
            if not await lane_ok(change.lane, Perm.CONFIG_WRITE):
                raise HTTPException(status.HTTP_403_FORBIDDEN, refused)
        refs = [c for c in body.changes if c.key in REF_KINDS and c.value.lower() != "off"]
        if refs:
            own = await _own_vault_ids(agent, service, user.id)
            for change in refs:
                if own.get(change.value.strip().lower()) != REF_KINDS[change.key]:
                    raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_YOUR_KEY)
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, refused)
