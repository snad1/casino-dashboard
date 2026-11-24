"""The role to permission matrix and the `require` dependency every guarded route uses."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, HTTPException, Path, Request, status
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
    KEYS_MANAGE = "keys.manage"
    LANES_CREATE = "lanes.create"
    KEYS_OWN = "keys.own"
    COMMENTS_MODERATE = "comments.moderate"


_VIEWER = frozenset({Perm.VIEW, Perm.COMMENT})
_OPERATOR = _VIEWER | {Perm.CONTROL_BASIC, Perm.CONFIG_WRITE, Perm.EXPORT, Perm.LOGS}
_ADMIN = frozenset(Perm) - {Perm.TERMINAL}

ROLE_PERMS: dict[Role, frozenset[Perm]] = {
    Role.OWNER: frozenset(Perm),
    Role.ADMIN: _ADMIN,
    Role.OPERATOR: _OPERATOR,
    Role.VIEWER: _VIEWER,
    # A friend running their own lanes: create one, use their own keys. Each lane they create
    # comes with an operator role on that lane alone, never on the service.
    Role.LANE_MANAGER: frozenset({Perm.LANES_CREATE, Perm.KEYS_OWN}),
    # A Telegram user reaches only their own bot user, through `tg_access`, never a service.
    Role.TG_USER: frozenset(),
}
# Until they turn on 2FA, these roles may only look: a stolen password alone controls nothing.
NEEDS_TWO_FACTOR = frozenset({Role.OWNER, Role.ADMIN})


async def roles_for(
    session: AsyncSession, user_id: int, service: str | None, lane: int | None = None
) -> set[Role]:
    """The user's global roles, plus the roles on `service` when one is named.

    A role on one lane counts only when `lane` names that lane, so a lane-only role never
    opens a service-wide check.
    """
    applies: ColumnElement[bool] = UserRole.service_id.is_(None)
    if service is not None:
        on_service = select(Service.id).where(Service.name == service).scalar_subquery()
        lanes: ColumnElement[bool] = UserRole.lane.is_(None)
        if lane is not None:
            lanes = or_(lanes, UserRole.lane == lane)
        applies = or_(applies, (UserRole.service_id == on_service) & lanes)
    rows = await session.scalars(select(UserRole.role).where(UserRole.user_id == user_id, applies))
    return {Role(role) for role in rows}


async def denial(
    session: AsyncSession, user: User, service: str | None, perm: Perm, lane: int | None = None
) -> str | None:
    """Why `user` may not use `perm` on `service`, or None when they may.

    An unknown service just grants nothing, so the refusal does not reveal which services
    exist. A grant that only an owner or admin role gives needs 2FA turned on.
    """
    if user.disabled:
        return FORBIDDEN
    roles = await roles_for(session, user.id, service, lane)
    if not any(perm in ROLE_PERMS[role] for role in roles):
        return FORBIDDEN
    if perm != Perm.VIEW and not any(perm in ROLE_PERMS[role] for role in roles - NEEDS_TWO_FACTOR):
        if not await is_enabled(session, user.id):
            return TWO_FACTOR_NEEDED
    return None


def require(perm: Perm) -> Callable[..., Awaitable[User]]:
    """Dependency that returns the user when one of their roles grants `perm`.

    Roles on the route's `{service}` path parameter count, as do global roles.
    """

    async def dependency(
        request: Request,
        user: Annotated[User, Depends(current_user)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> User:
        detail = await denial(session, user, request.path_params.get("service"), perm)
        if detail is not None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail)
        return user

    return dependency


async def lane_scope(
    session: AsyncSession, user: User, service: str, perm: Perm
) -> frozenset[int] | None:
    """The lanes of `service` where `user` may use `perm`: None for every lane, a set when
    only lane roles grant it (empty when nothing does)."""
    if await denial(session, user, service, perm) is None:
        return None
    rows = await session.scalars(
        select(UserRole.lane)
        .join(Service, Service.id == UserRole.service_id)
        .where(UserRole.user_id == user.id, Service.name == service, UserRole.lane.is_not(None))
    )
    lanes = set()
    for lane in set(rows):
        if lane is not None and await denial(session, user, service, perm, lane) is None:
            lanes.add(lane)
    return frozenset(lanes)


@dataclass(frozen=True)
class LaneScope:
    """Who is asking and which lanes they may see; `lanes` is None for every lane."""

    user: User
    lanes: frozenset[int] | None

    def allows(self, lane: int | None) -> bool:
        return self.lanes is None or (lane is not None and lane in self.lanes)

    def check(self, lane: int | None) -> None:
        """Refuse a lane filter naming a lane outside the scope; no filter is fine."""
        if lane is not None and not self.allows(lane):
            raise HTTPException(status.HTTP_403_FORBIDDEN, FORBIDDEN)

    def one(self, lane: int | None) -> int | None:
        """The lane a single-lane report covers: the asked one when allowed, or the only lane
        a lane-only user holds. Refuses a lane-only user's report over several lanes."""
        self.check(lane)
        if lane is not None or self.lanes is None:
            return lane
        if len(self.lanes) != 1:
            raise HTTPException(status.HTTP_403_FORBIDDEN, FORBIDDEN)
        return next(iter(self.lanes))

    def cache_tag(self) -> str:
        """Added to cache keys so an answer cut to some lanes never serves another caller."""
        return "" if self.lanes is None else "&lanes=" + ",".join(map(str, sorted(self.lanes)))


def scoped(perm: Perm) -> Callable[..., Awaitable[LaneScope]]:
    """Dependency like `require`, which also lets in a user whose lane roles grant `perm`,
    with the lanes they may see."""

    async def dependency(
        request: Request,
        user: Annotated[User, Depends(current_user)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> LaneScope:
        service = request.path_params.get("service")
        if service is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, FORBIDDEN)
        lanes = await lane_scope(session, user, service, perm)
        if lanes is not None and not lanes:
            detail = await denial(session, user, service, perm)
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail or FORBIDDEN)
        return LaneScope(user, lanes)

    return dependency


TgUid = Annotated[int, Path(ge=1, le=2**63 - 1)]


async def tg_denial(session: AsyncSession, user: User, uid: int) -> str | None:
    """Why `user` may not see or control Telegram user `uid`, or None when they may.

    Their own Telegram account may; so may whoever manages users (owner, admin, with 2FA as
    for any grant beyond `view`). Every other caller gets the same refusal, whether or not
    `uid` exists.
    """
    if user.disabled:
        return FORBIDDEN
    if user.tg_uid == uid and Role.TG_USER in await roles_for(session, user.id, None):
        return None
    if user.tg_uid is not None:
        return FORBIDDEN
    return await denial(session, user, None, Perm.USERS_MANAGE)


async def tg_access(
    uid: TgUid,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Dependency for every `/tg/{uid}/...` route: the caller, once `tg_denial` allows them."""
    detail = await tg_denial(session, user, uid)
    if detail is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail)
    return user
