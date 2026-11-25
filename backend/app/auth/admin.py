"""User administration: list users, change a user's role, disable or enable an account.

Every route needs `users.manage` and a second factor confirmed in the last five minutes.
The single owner is made by the CLI and can never be changed, disabled or granted here, and
an admin cannot act on their own account, so nobody can lock themselves or the owner out.
Telegram accounts are not listed here and keep their own role: they see only their own bot
user, and making one an admin would hand a bot login link dashboard rights.
Disabling an account also ends all of its sessions. The audit middleware records each call.
A role may name one lane of a service; such a role grants nothing outside that lane, and
only a lane the bot has can be named. Only the owner sets how many lanes a lane manager may
create.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_client import AgentError
from app.api.control import AGENT_DOWN, CONTROLS, agent_or_error
from app.api.keys import KEY_ID_PATTERN
from app.api.lane_access import registered_keys, registered_lanes
from app.auth.models import INVITE_LANES_MAX, Role, Totp, User, UserRole
from app.auth.permissions import Perm, require, roles_for
from app.auth.sessions import revoke_all
from app.auth.stepup import require_fresh
from app.db.engine import get_session
from app.db.models import Service

USER_NOT_FOUND = "That user does not exist."
OWNER_LOCKED = "The owner account can't be changed here."
NOT_YOURSELF = "You can't change your own account here."
UNKNOWN_SERVICE = "That service does not exist."
TELEGRAM_ROLE = "A Telegram account keeps its own role."
LANE_NEEDS_SERVICE = "Pick the service the lane belongs to."
LANE_ROLES = ("viewer", "operator")
LANE_ROLE = "Only viewer or operator can be given on one lane."
LANES_TWICE = "Each lane may be picked once."
MANAGER_NEEDS_SERVICE = "Pick the service a lane manager runs lanes on."
LANE_UNKNOWN = "That lane does not exist."
KEY_NEEDS_SERVICE = "Pick the service the key belongs to."
KEY_ROLE = "Only viewer or operator can be given on keys."
KEYS_TWICE = "Each key may be picked once."
KEYS_WITH_LANES = "A role is limited by lanes or by keys, not both."
KEY_UNKNOWN = "That key does not exist."
OWNER_ONLY = "Only the owner can change lane quotas."

log = structlog.get_logger("app.admin")

router = APIRouter(prefix="/admin/users", tags=["admin"])
Db = Annotated[AsyncSession, Depends(get_session)]
Manager = Annotated[User, Depends(require(Perm.USERS_MANAGE))]
Fresh = Annotated[User, Depends(require_fresh())]
UserId = Annotated[int, Path(ge=1)]


class RoleOut(BaseModel):
    service: str | None
    role: str
    lane: int | None = None
    key_ref: str | None = None


class UserOut(BaseModel):
    id: int
    email: str
    roles: list[RoleOut]
    disabled: bool
    totp_enabled: bool
    lane_quota: int
    created_at: datetime


class UserList(BaseModel):
    items: list[UserOut]


Lane = Annotated[int, Field(ge=1, le=99)]
Lanes = Annotated[list[Lane], Field(default_factory=list, max_length=INVITE_LANES_MAX)]
Key = Annotated[str, Field(pattern=KEY_ID_PATTERN)]
Keys = Annotated[list[Key], Field(default_factory=list, max_length=INVITE_LANES_MAX)]


def check_scope(
    role: str, service: str | None, lanes: Sequence[int], keys: Sequence[str] = ()
) -> None:
    """Refuse a role and scope that can't go together: a lane manager, lanes or keys need a
    service, only viewer or operator can be given on lanes or keys, and never on both."""
    if role == "lane_manager" and service is None:
        raise ValueError(MANAGER_NEEDS_SERVICE)
    if lanes and keys:
        raise ValueError(KEYS_WITH_LANES)
    if lanes and service is None:
        raise ValueError(LANE_NEEDS_SERVICE)
    if lanes and role not in LANE_ROLES:
        raise ValueError(LANE_ROLE)
    if keys and service is None:
        raise ValueError(KEY_NEEDS_SERVICE)
    if keys and role not in LANE_ROLES:
        raise ValueError(KEY_ROLE)


def distinct_lanes(lanes: list[int]) -> list[int]:
    """The lanes in order; a lane given twice is refused."""
    if len(set(lanes)) != len(lanes):
        raise ValueError(LANES_TWICE)
    return sorted(lanes)


def distinct_keys(keys: list[str]) -> list[str]:
    """The keys in order; a key given twice is refused."""
    if len(set(keys)) != len(keys):
        raise ValueError(KEYS_TWICE)
    return sorted(keys)


async def require_keys(request: Request, service: str, keys: Sequence[str]) -> None:
    """Refuse a key the bot's vault does not hold (422), or 502 when the bot host can't say.

    A role on a key id that doesn't exist yet would open whichever key gets that id later.
    """
    if service not in CONTROLS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, KEY_UNKNOWN)
    try:
        known = await registered_keys(agent_or_error(request), service)
    except AgentError as error:
        log.warning("key list failed while granting a key", code=error.code)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, AGENT_DOWN) from None
    if not set(keys) <= known:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, KEY_UNKNOWN)


def role_rows(
    user_id: int, service_id: int | None, role: Role, lanes: Sequence[int], keys: Sequence[str]
) -> list[UserRole]:
    """One role row per lane, or per key, or one for the whole service or every service."""
    if keys:
        return [
            UserRole(user_id=user_id, service_id=service_id, role=role, key_ref=key) for key in keys
        ]
    scopes: list[int | None] = [*lanes] or [None]
    return [
        UserRole(user_id=user_id, service_id=service_id, role=role, lane=lane) for lane in scopes
    ]


async def require_lanes(request: Request, service: str, lanes: Sequence[int]) -> None:
    """Refuse a lane the bot does not have (422), or 502 when the bot host can't say.

    A role on a lane id that doesn't exist yet would open whichever lane gets that id later.
    """
    if service not in CONTROLS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, LANE_UNKNOWN)
    try:
        known = await registered_lanes(agent_or_error(request), service)
    except AgentError as error:
        log.warning("lane list failed while granting a lane", code=error.code)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, AGENT_DOWN) from None
    if not set(lanes) <= known:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, LANE_UNKNOWN)


class RoleIn(BaseModel):
    role: Literal["admin", "operator", "viewer", "lane_manager"]
    service: str | None = Field(default=None, max_length=100)
    lanes: Lanes
    keys: Keys

    _distinct = field_validator("lanes")(distinct_lanes)
    _distinct_keys = field_validator("keys")(distinct_keys)

    @model_validator(mode="after")
    def _lanes_in_a_service(self) -> "RoleIn":
        check_scope(self.role, self.service, self.lanes, self.keys)
        return self


class LaneQuotaIn(BaseModel):
    lane_quota: int = Field(ge=1, le=99)


@router.get("", response_model=UserList)
async def list_users(_: Manager, db: Db) -> UserList:
    """Every account, oldest first, with its roles and whether 2FA is on."""
    users = list(await db.scalars(select(User).where(User.tg_uid.is_(None)).order_by(User.id)))
    rows = await db.execute(
        select(UserRole.user_id, Service.name, UserRole.role, UserRole.lane, UserRole.key_ref)
        .outerjoin(Service, Service.id == UserRole.service_id)
        .order_by(
            Service.name.nulls_first(), UserRole.lane.nulls_first(), UserRole.key_ref.nulls_first()
        )
    )
    roles: dict[int, list[RoleOut]] = {}
    for user_id, service, role, lane, key_ref in rows:
        roles.setdefault(user_id, []).append(
            RoleOut(service=service, role=role, lane=lane, key_ref=key_ref)
        )
    with_totp = set(await db.scalars(select(Totp.user_id).where(Totp.enabled_at.is_not(None))))
    return UserList(
        items=[
            UserOut(
                id=user.id,
                email=user.email or "",
                roles=roles.get(user.id, []),
                disabled=user.disabled,
                totp_enabled=user.id in with_totp,
                lane_quota=user.lane_quota,
                created_at=user.created_at,
            )
            for user in users
        ]
    )


async def _target(db: AsyncSession, actor: User, user_id: int) -> User:
    if user_id == actor.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_YOURSELF)
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, USER_NOT_FOUND)
    is_owner = await db.scalar(
        select(UserRole.user_id).where(UserRole.user_id == user_id, UserRole.role == Role.OWNER)
    )
    if is_owner is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, OWNER_LOCKED)
    return user


@router.put("/{user_id}/role", status_code=status.HTTP_204_NO_CONTENT)
async def set_role(
    user_id: UserId, body: RoleIn, request: Request, actor: Manager, _: Fresh, db: Db
) -> None:
    """Replace the user's roles with one role: on all services, one service, or some of its
    lanes or keys (one row each)."""
    user = await _target(db, actor, user_id)
    if user.tg_uid is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, TELEGRAM_ROLE)
    service_id = None
    if body.service is not None:
        service_id = await db.scalar(select(Service.id).where(Service.name == body.service))
        if service_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, UNKNOWN_SERVICE)
    if body.service is not None and body.lanes:
        await require_lanes(request, body.service, body.lanes)
    if body.service is not None and body.keys:
        await require_keys(request, body.service, body.keys)
    await db.execute(delete(UserRole).where(UserRole.user_id == user.id))
    db.add_all(role_rows(user.id, service_id, Role(body.role), body.lanes, body.keys))
    await db.commit()


@router.post("/{user_id}/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable_user(user_id: UserId, actor: Manager, _: Fresh, db: Db) -> None:
    """Block sign-in and end every session the user has open."""
    user = await _target(db, actor, user_id)
    user.disabled = True
    await revoke_all(db, user.id)
    await db.commit()


@router.post("/{user_id}/enable", status_code=status.HTTP_204_NO_CONTENT)
async def enable_user(user_id: UserId, actor: Manager, _: Fresh, db: Db) -> None:
    user = await _target(db, actor, user_id)
    user.disabled = False
    await db.commit()


async def _owner(actor: Manager, db: Db) -> User:
    """The caller when they are the owner; anyone else is refused before the second factor
    is asked for."""
    if Role.OWNER not in await roles_for(db, actor.id, None):
        raise HTTPException(status.HTTP_403_FORBIDDEN, OWNER_ONLY)
    return actor


@router.put("/{user_id}/lane-quota", status_code=status.HTTP_204_NO_CONTENT)
async def set_lane_quota(
    user_id: UserId,
    body: LaneQuotaIn,
    actor: Annotated[User, Depends(_owner)],
    _: Fresh,
    db: Db,
) -> None:
    """Set how many lanes the user may create as a lane manager; the owner only."""
    user = await _target(db, actor, user_id)
    user.lane_quota = body.lane_quota
    await db.commit()
