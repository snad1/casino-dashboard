"""The role to permission matrix and the `require` dependency every guarded route uses."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, HTTPException, Path, Request, status
from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.key_lanes import KEY_LANES, MAIN_KEY
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

    A role on one lane, or on one key, counts only when `lane` names that lane or a lane that
    bets with that key now, so neither ever opens a service-wide check.
    """
    applies: ColumnElement[bool] = UserRole.service_id.is_(None)
    if service is not None:
        on_service = select(Service.id).where(Service.name == service).scalar_subquery()
        lanes: ColumnElement[bool] = UserRole.lane.is_(None)
        if lane is not None:
            lanes = or_(lanes, UserRole.lane == lane)
        applies = or_(applies, (UserRole.service_id == on_service) & lanes)
    rows = await session.execute(
        select(UserRole.role, UserRole.key_ref).where(UserRole.user_id == user_id, applies)
    )
    roles: set[Role] = set()
    keyed: list[tuple[str, str]] = []
    for role, key_ref in rows:
        if key_ref is None:
            roles.add(Role(role))
        else:
            keyed.append((role, key_ref))
    if keyed and service is not None and lane is not None:
        by_key = await KEY_LANES.lanes(service)
        roles |= {Role(role) for role, key in keyed if lane in by_key.get(key, ())}
    return roles


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


@dataclass(frozen=True)
class LaneScope:
    """Who is asking and what they may see.

    `lanes` is None for every lane, else the lanes they hold a lane role on plus the lanes
    betting with one of their keys now. `keys` is None unless key roles grant it: history
    is then cut to the sessions that bet with those keys (a lane may have used another key
    before), and `own_lanes` keeps the lanes held by lane role, whose history stays whole.
    """

    user: User
    lanes: frozenset[int] | None
    keys: frozenset[str] | None = None
    own_lanes: frozenset[int] = frozenset()

    @property
    def keyed(self) -> bool:
        return self.keys is not None

    def allows(self, lane: int | None) -> bool:
        return self.lanes is None or (lane is not None and lane in self.lanes)

    def allows_session(self, lane: int | None, key_ref: str | None) -> bool:
        """Whether a recorded session is in the scope; a session with no key used the main one."""
        if self.keys is None:
            return self.allows(lane)
        return (key_ref or MAIN_KEY) in self.keys or (lane is not None and lane in self.own_lanes)

    def check(self, lane: int | None) -> None:
        """Refuse a lane filter naming a lane outside the scope; no filter is fine. A key
        scope filters history by key as well, so any lane may narrow it further."""
        if lane is not None and not self.keyed and not self.allows(lane):
            raise HTTPException(status.HTTP_403_FORBIDDEN, FORBIDDEN)

    def check_key(self, key: str | None) -> None:
        """Refuse a key filter naming a key outside a key scope; no filter is fine."""
        if key is not None and self.keys is not None and key not in self.keys:
            raise HTTPException(status.HTTP_403_FORBIDDEN, FORBIDDEN)

    def report_keys(self, key: str | None) -> list[str] | None:
        """The keys a report over sessions keeps: the asked one, else a key scope's keys."""
        self.check_key(key)
        if key is not None:
            return [key]
        return None if self.keys is None else sorted(self.keys)

    def one(self, lane: int | None) -> int | None:
        """The lane a single-lane report covers: the asked one when allowed, or the only lane
        a lane-only user holds. Refuses a lane-only user's report over several lanes. A key
        scope's report is read from history by key, so it needs no single lane."""
        self.check(lane)
        if lane is not None or self.lanes is None or self.keyed:
            return lane
        if len(self.lanes) != 1:
            raise HTTPException(status.HTTP_403_FORBIDDEN, FORBIDDEN)
        return next(iter(self.lanes))

    def cache_tag(self) -> str:
        """Added to cache keys so an answer cut to some lanes or keys never serves another
        caller."""
        if self.lanes is None:
            return ""
        tag = "&lanes=" + ",".join(map(str, sorted(self.lanes)))
        if self.keys is not None:
            tag += (
                "&keys="
                + ",".join(sorted(self.keys))
                + "&own="
                + ",".join(map(str, sorted(self.own_lanes)))
            )
        return tag


async def lane_scope(session: AsyncSession, user: User, service: str, perm: Perm) -> LaneScope:
    """Where on `service` `user` may use `perm`: every lane, or the lanes their lane and key
    roles grant it on (none when nothing does)."""
    if await denial(session, user, service, perm) is None:
        return LaneScope(user, None)
    rows = await session.execute(
        select(UserRole.role, UserRole.lane, UserRole.key_ref)
        .join(Service, Service.id == UserRole.service_id)
        .where(
            UserRole.user_id == user.id,
            Service.name == service,
            or_(UserRole.lane.is_not(None), UserRole.key_ref.is_not(None)),
        )
    )
    granted = [(lane, key) for role, lane, key in rows if perm in ROLE_PERMS[Role(role)]]
    if user.disabled or not granted:
        return LaneScope(user, frozenset())
    own = frozenset(lane for lane, _ in granted if lane is not None)
    keys = frozenset(key for _, key in granted if key is not None)
    lanes = set(own)
    if keys:
        by_key = await KEY_LANES.lanes(service)
        for key in keys:
            lanes |= by_key.get(key, frozenset())
    allowed = set()
    for lane in lanes:
        if await denial(session, user, service, perm, lane) is None:
            allowed.add(lane)
    candidates = frozenset(allowed)
    if not keys:
        return LaneScope(user, candidates)
    return LaneScope(user, candidates, keys, own & candidates)


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
        scope = await lane_scope(session, user, service, perm)
        if scope.lanes is not None and not scope.lanes and not scope.keys:
            detail = await denial(session, user, service, perm)
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail or FORBIDDEN)
        return scope

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
