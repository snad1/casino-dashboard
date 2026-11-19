"""User administration: list users, change a user's role, disable or enable an account.

Every route needs `users.manage` and a second factor confirmed in the last five minutes.
The single owner is made by the CLI and can never be changed, disabled or granted here, and
an admin cannot act on their own account, so nobody can lock themselves or the owner out.
Disabling an account also ends all of its sessions. The audit middleware records each call.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Role, Totp, User, UserRole
from app.auth.permissions import Perm, require
from app.auth.sessions import revoke_all
from app.auth.stepup import require_fresh
from app.db.engine import get_session
from app.db.models import Service

USER_NOT_FOUND = "That user does not exist."
OWNER_LOCKED = "The owner account can't be changed here."
NOT_YOURSELF = "You can't change your own account here."
UNKNOWN_SERVICE = "That service does not exist."

router = APIRouter(prefix="/admin/users", tags=["admin"])
Db = Annotated[AsyncSession, Depends(get_session)]
Manager = Annotated[User, Depends(require(Perm.USERS_MANAGE))]
Fresh = Annotated[User, Depends(require_fresh())]
UserId = Annotated[int, Path(ge=1)]


class RoleOut(BaseModel):
    service: str | None
    role: str


class UserOut(BaseModel):
    id: int
    email: str
    roles: list[RoleOut]
    disabled: bool
    totp_enabled: bool
    created_at: datetime


class UserList(BaseModel):
    items: list[UserOut]


class RoleIn(BaseModel):
    role: Literal["admin", "operator", "viewer"]
    service: str | None = Field(default=None, max_length=100)


@router.get("", response_model=UserList)
async def list_users(_: Manager, db: Db) -> UserList:
    """Every account, oldest first, with its roles and whether 2FA is on."""
    users = list(await db.scalars(select(User).order_by(User.id)))
    role_rows = await db.execute(
        select(UserRole.user_id, Service.name, UserRole.role)
        .outerjoin(Service, Service.id == UserRole.service_id)
        .order_by(Service.name.nulls_first())
    )
    roles: dict[int, list[RoleOut]] = {}
    for user_id, service, role in role_rows:
        roles.setdefault(user_id, []).append(RoleOut(service=service, role=role))
    with_totp = set(await db.scalars(select(Totp.user_id).where(Totp.enabled_at.is_not(None))))
    return UserList(
        items=[
            UserOut(
                id=user.id,
                email=user.email,
                roles=roles.get(user.id, []),
                disabled=user.disabled,
                totp_enabled=user.id in with_totp,
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
async def set_role(user_id: UserId, body: RoleIn, actor: Manager, _: Fresh, db: Db) -> None:
    """Replace the user's roles with one role, on one service or (no service) on all."""
    user = await _target(db, actor, user_id)
    service_id = None
    if body.service is not None:
        service_id = await db.scalar(select(Service.id).where(Service.name == body.service))
        if service_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, UNKNOWN_SERVICE)
    await db.execute(delete(UserRole).where(UserRole.user_id == user.id))
    db.add(UserRole(user_id=user.id, service_id=service_id, role=Role(body.role)))
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
