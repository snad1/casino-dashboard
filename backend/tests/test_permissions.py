"""Every role x permission pair, and the `require` dependency against real role rows."""

import uuid
from collections.abc import Iterator
from typing import Annotated

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Role, User, UserRole
from app.auth.permissions import FORBIDDEN, ROLE_PERMS, Perm, require
from app.auth.sessions import NOT_SIGNED_IN, current_user
from app.db.models import Service
from app.main import create_app
from app.settings import Settings
from tests.conftest import run_in_session

NO_LOGIN = "!"
P = Perm
EXPECTED: dict[Perm, set[Role]] = {
    P.VIEW: {Role.OWNER, Role.ADMIN, Role.OPERATOR, Role.VIEWER},
    P.COMMENT: {Role.OWNER, Role.ADMIN, Role.OPERATOR, Role.VIEWER},
    P.CONTROL_BASIC: {Role.OWNER, Role.ADMIN, Role.OPERATOR},
    P.CONFIG_WRITE: {Role.OWNER, Role.ADMIN, Role.OPERATOR},
    P.EXPORT: {Role.OWNER, Role.ADMIN, Role.OPERATOR},
    P.LOGS: {Role.OWNER, Role.ADMIN, Role.OPERATOR},
    P.CONTROL_DESTRUCTIVE: {Role.OWNER, Role.ADMIN},
    P.USERS_MANAGE: {Role.OWNER, Role.ADMIN},
    P.AUDIT_READ: {Role.OWNER, Role.ADMIN},
    P.ALERTS_MANAGE: {Role.OWNER, Role.ADMIN},
    P.TERMINAL: {Role.OWNER},
}


def test_expected_table_covers_every_permission() -> None:
    assert set(EXPECTED) == set(Perm)
    assert set(ROLE_PERMS) == set(Role)


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("perm", list(Perm))
def test_matrix(role: Role, perm: Perm) -> None:
    assert (perm in ROLE_PERMS[role]) == (role in EXPECTED[perm])


def test_without_a_session_cookie_nobody_is_signed_in(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/s/{service}/view")
    async def view(user: Annotated[User, Depends(require(Perm.VIEW))]) -> int:
        return user.id

    response = TestClient(app).get("/s/wolfbet/view")
    assert response.status_code == 401
    assert response.json() == {"detail": NOT_SIGNED_IN}


Seeded = tuple[Settings, str, dict[str, User]]


@pytest.fixture
def seeded(migrated: Settings) -> Iterator[Seeded]:
    """Committed rows (the app uses its own connections): a service and one user per case."""
    tag = uuid.uuid4().hex[:8]
    service, other = f"wolfbet-{tag}", f"goated-{tag}"

    async def setup(session: AsyncSession) -> dict[str, User]:
        services = [Service(name=n, adapter="wolfbet", host=f"{n}.test") for n in (service, other)]
        session.add_all(services)
        users = {
            name: User(email=f"{name}-{tag}@example.test", password_hash=NO_LOGIN)
            for name in ("viewer", "operator", "other_operator", "global_admin", "disabled", "none")
        }
        users["disabled"].disabled = True
        session.add_all(users.values())
        await session.flush()
        on, off = services[0].id, services[1].id
        session.add_all(
            [
                UserRole(user_id=users["viewer"].id, service_id=on, role=Role.VIEWER),
                UserRole(user_id=users["operator"].id, service_id=on, role=Role.OPERATOR),
                UserRole(user_id=users["other_operator"].id, service_id=off, role=Role.OPERATOR),
                UserRole(user_id=users["global_admin"].id, service_id=None, role=Role.ADMIN),
                UserRole(user_id=users["disabled"].id, service_id=on, role=Role.OPERATOR),
            ]
        )
        await session.commit()
        return users

    async def teardown(session: AsyncSession) -> None:
        await session.execute(delete(User).where(User.email.endswith(f"-{tag}@example.test")))
        await session.execute(delete(Service).where(Service.name.in_([service, other])))
        await session.commit()

    users = run_in_session(migrated, setup)
    try:
        yield migrated, service, users
    finally:
        run_in_session(migrated, teardown)


@pytest.mark.parametrize(
    ("who", "perm", "status"),
    [
        ("viewer", Perm.VIEW, 200),
        ("viewer", Perm.CONTROL_BASIC, 403),
        ("operator", Perm.CONTROL_BASIC, 200),
        ("operator", Perm.CONTROL_DESTRUCTIVE, 403),
        ("other_operator", Perm.VIEW, 403),
        ("global_admin", Perm.CONTROL_DESTRUCTIVE, 200),
        ("global_admin", Perm.TERMINAL, 403),
        ("disabled", Perm.VIEW, 403),
        ("none", Perm.VIEW, 403),
    ],
)
def test_require_on_a_service_route(seeded: Seeded, who: str, perm: Perm, status: int) -> None:
    settings, service, users = seeded
    app = create_app(settings)

    @app.get("/s/{service}/check")
    async def check(user: Annotated[User, Depends(require(perm))]) -> int:
        return user.id

    app.dependency_overrides[current_user] = lambda: users[who]
    with TestClient(app) as client:
        response = client.get(f"/s/{service}/check")
    assert response.status_code == status
    if status == 403:
        assert response.json() == {"detail": FORBIDDEN}


def test_unknown_service_is_forbidden_not_missing(
    seeded: Seeded,
) -> None:
    settings, _, users = seeded
    app = create_app(settings)

    @app.get("/s/{service}/check")
    async def check(user: Annotated[User, Depends(require(Perm.CONTROL_BASIC))]) -> int:
        return user.id

    app.dependency_overrides[current_user] = lambda: users["operator"]
    with TestClient(app) as client:
        assert client.get("/s/no-such-service/check").status_code == 403


def test_global_role_applies_to_routes_without_a_service(
    seeded: Seeded,
) -> None:
    settings, _, users = seeded
    app = create_app(settings)

    @app.get("/users")
    async def manage(user: Annotated[User, Depends(require(Perm.USERS_MANAGE))]) -> int:
        return user.id

    with TestClient(app) as client:
        app.dependency_overrides[current_user] = lambda: users["global_admin"]
        assert client.get("/users").status_code == 200
        app.dependency_overrides[current_user] = lambda: users["operator"]
        assert client.get("/users").status_code == 403
