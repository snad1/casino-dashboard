"""The role to permission matrix and the `require` dependency every guarded route uses."""

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Role, User, UserRole
from app.auth.sessions import current_user
from app.auth.totp import is_enabled
from app.db.engine import get_session
from app.db.models import Service

FORBIDDEN = "You do not have permission to do that."
TWO_FACTOR_NEEDED = "Turn on two-factor authentication to use this."


class Perm(StrEnum):
    VIEW = "view"
    COMMENT = "comment"
    CONTROL_BASIC = "control.basic"
    CONTROL_DESTRUCTIVE = "control.destructive"
    CONFIG_WRITE = "config.write"
    EXPORT = "export"
    LOGS = "logs"
    TERMINAL = "terminal"
    USERS_MANAGE = "users.manage"
    AUDIT_READ = "audit.read"
    ALERTS_MANAGE = "alerts.manage"


_VIEWER = frozenset({Perm.VIEW, Perm.COMMENT})
_OPERATOR = _VIEWER | {Perm.CONTROL_BASIC, Perm.CONFIG_WRITE, Perm.EXPORT, Perm.LOGS}
_ADMIN = frozenset(Perm) - {Perm.TERMINAL}

ROLE_PERMS: dict[Role, frozenset[Perm]] = {
    Role.OWNER: frozenset(Perm),
    Role.ADMIN: _ADMIN,
    Role.OPERATOR: _OPERATOR,
    Role.VIEWER: _VIEWER,
}
# Until they turn on 2FA, these roles may only look: a stolen password alone controls nothing.
NEEDS_TWO_FACTOR = frozenset({Role.OWNER, Role.ADMIN})


async def roles_for(session: AsyncSession, user_id: int, service: str | None) -> set[Role]:
    """The user's global roles, plus the roles on `service` when one is named."""
    applies: ColumnElement[bool] = UserRole.service_id.is_(None)
    if service is not None:
        on_service = select(Service.id).where(Service.name == service).scalar_subquery()
        applies = or_(applies, UserRole.service_id == on_service)
    rows = await session.scalars(select(UserRole.role).where(UserRole.user_id == user_id, applies))
    return {Role(role) for role in rows}


def require(perm: Perm) -> Callable[..., Awaitable[User]]:
    """Dependency that returns the user when one of their roles grants `perm`.

    Roles on the route's `{service}` path parameter count, as do global roles. An unknown
    service just grants nothing, so the 403 does not reveal which services exist. A grant
    that only an owner or admin role gives needs 2FA turned on.
    """

    async def dependency(
        request: Request,
        user: Annotated[User, Depends(current_user)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> User:
        if user.disabled:
            raise HTTPException(status.HTTP_403_FORBIDDEN, FORBIDDEN)
        roles = await roles_for(session, user.id, request.path_params.get("service"))
        if not any(perm in ROLE_PERMS[role] for role in roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, FORBIDDEN)
        if perm != Perm.VIEW and not any(
            perm in ROLE_PERMS[role] for role in roles - NEEDS_TWO_FACTOR
        ):
            if not await is_enabled(session, user.id):
                raise HTTPException(status.HTTP_403_FORBIDDEN, TWO_FACTOR_NEEDED)
        return user

    return dependency
