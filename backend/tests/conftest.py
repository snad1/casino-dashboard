"""Every test gets valid secret files and a testing environment; DB tests get real Timescale."""

import asyncio
import hashlib
import secrets
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from app.auth.models import Role, Totp, User, UserRole
from app.auth.passwords import hash_password
from app.auth.sessions import current_user
from app.main import create_app
from app.settings import Settings, load_settings

# WolfBet SQLite builder and the app-role Timescale connection used by the ingest tests;
# a fake host agent and signed-in clients for the agent, control and config tests.
pytest_plugins = ["tests.wolfbet_db", "tests.fake_agent", "tests.control_rig"]

REQUIRED_SECRETS = (
    "session_secret",
    "totp_key",
    "postgres_password",
    "redis_dashboard_password",
)
PUBLIC_HOST = "dash.example.test"


@pytest.fixture(autouse=True)
def secrets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    directory = tmp_path / "secrets"
    directory.mkdir()
    for name in REQUIRED_SECRETS:
        (directory / name).write_text(secrets.token_urlsafe(48))
    monkeypatch.setenv("SECRETS_DIR", str(directory))
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("PUBLIC_HOST", PUBLIC_HOST)
    monkeypatch.setenv("LIVE_SERVICE", "")
    yield directory


@pytest.fixture
def settings() -> Settings:
    return load_settings()


TIMESCALE_SERVICE = "timescale"
REDIS_SERVICE = "redis"
COMPOSE_FILE = Path(__file__).resolve().parents[2] / "deploy" / "compose.yml"
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def compose_image(service: str) -> str:
    """The image the deployed stack runs, so tests never drift from production."""
    compose = yaml.safe_load(COMPOSE_FILE.read_text())
    image: str = compose["services"][service]["image"]
    return image


def timescale_image() -> str:
    return compose_image(TIMESCALE_SERVICE)


@pytest.fixture(scope="session")
def db_settings(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Settings]:
    """A throwaway Timescale container, started once and only when a test needs a database."""
    password = secrets.token_urlsafe(48)
    with PostgresContainer(
        timescale_image(), username="dashboard", password=password, dbname="dashboard"
    ) as container:
        directory = tmp_path_factory.mktemp("db-secrets")
        for name in REQUIRED_SECRETS:
            (directory / name).write_text(secrets.token_urlsafe(48))
        (directory / "postgres_password").write_text(password)
        yield Settings(
            app_env="testing",
            public_host=PUBLIC_HOST,
            postgres_host=container.get_container_host_ip(),
            postgres_port=int(container.get_exposed_port(5432)),
            live_service=None,
            _secrets_dir=str(directory),
        )


APP_ROLE = "dashboard_app"
# One password per test run; every migration run (re)sets the api role to it.
APP_ROLE_PASSWORD = secrets.token_urlsafe(48)


def alembic_config(settings: Settings) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.attributes["url"] = settings.database_url
    config.attributes["app_password"] = APP_ROLE_PASSWORD
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def migrated(db_settings: Settings) -> Settings:
    command.upgrade(alembic_config(db_settings), "head")
    return db_settings


def run_in_session[T](settings: Settings, step: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Run `step` in its own committed session, for rows a separate app connection must see."""

    async def run() -> T:
        engine = create_async_engine(settings.database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                return await step(session)
        finally:
            await engine.dispose()

    return asyncio.run(run())


@pytest.fixture
async def db_session(migrated: Settings) -> AsyncIterator[AsyncSession]:
    """A session inside one outer transaction that is rolled back after the test."""
    engine = create_async_engine(migrated.database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


ACL_TEMPLATE = COMPOSE_FILE.parent / "redis" / "users.acl.tmpl"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class RedisServer:
    host: str
    port: int
    dashboard_password: str
    publisher_password: str

    def url(self, user: str, password: str) -> str:
        return f"redis://{user}:{password}@{self.host}:{self.port}/0"


@pytest.fixture(scope="session")
def redis_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[RedisServer]:
    """Redis running the deployed ACL, with both users' passwords known to the tests."""
    dashboard_password = secrets.token_urlsafe(48)
    publisher_password = secrets.token_urlsafe(48)
    acl_dir = tmp_path_factory.mktemp("redis-acl")
    (acl_dir / "users.acl").write_text(
        ACL_TEMPLATE.read_text()
        .replace("PUBLISHER_HASH", _sha256(publisher_password))
        .replace("DASHBOARD_HASH", _sha256(dashboard_password))
    )
    container = (
        DockerContainer(compose_image(REDIS_SERVICE))
        .with_exposed_ports(6379)
        .with_volume_mapping(str(acl_dir), "/acl", "ro")
        .with_command("redis-server --aclfile /acl/users.acl --save '' --appendonly no")
        .waiting_for(LogMessageWaitStrategy("Ready to accept connections"))
    )
    with container:
        yield RedisServer(
            container.get_container_host_ip(),
            int(container.get_exposed_port(6379)),
            dashboard_password,
            publisher_password,
        )


@pytest.fixture(scope="session")
def app_settings(
    migrated: Settings, redis_server: RedisServer, tmp_path_factory: pytest.TempPathFactory
) -> Settings:
    """Migrated Timescale (as the api's own role) plus the Redis above, as the api sees them."""
    directory = tmp_path_factory.mktemp("app-secrets")
    for name in REQUIRED_SECRETS:
        (directory / name).write_text(secrets.token_urlsafe(48))
    (directory / "postgres_password").write_text(APP_ROLE_PASSWORD)
    (directory / "redis_dashboard_password").write_text(redis_server.dashboard_password)
    return Settings(
        app_env="testing",
        public_host=PUBLIC_HOST,
        postgres_host=migrated.postgres_host,
        postgres_port=migrated.postgres_port,
        postgres_user=APP_ROLE,
        redis_host=redis_server.host,
        redis_port=redis_server.port,
        live_service=None,
        _secrets_dir=str(directory),
    )


ORIGIN = f"https://{PUBLIC_HOST}"
PASSWORD = secrets.token_urlsafe(24)


@dataclass
class Account:
    id: int
    email: str


@pytest.fixture
async def sessions(app_settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions as the api's own role, so the grants on the tables a test writes are proven."""
    engine = create_async_engine(app_settings.database_url, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def make_client(settings: Settings) -> TestClient:
    """A client on its own made-up IP, so the per-IP limit never leaks between tests."""
    ip = f"10.{secrets.randbelow(256)}.{secrets.randbelow(256)}.{secrets.randbelow(254) + 1}"
    return TestClient(
        create_app(settings),
        base_url="https://testserver",
        client=(ip, 50000),
        headers={"origin": ORIGIN},
    )


@pytest.fixture
def client(app_settings: Settings) -> Iterator[TestClient]:
    with make_client(app_settings) as c:
        yield c


@pytest.fixture
def account(app_settings: Settings) -> Iterator[Account]:
    email = f"user-{uuid.uuid4().hex[:10]}@example.test"

    async def create(session: AsyncSession) -> Account:
        user = User(email=email, password_hash=hash_password(PASSWORD))
        session.add(user)
        await session.flush()
        session.add(UserRole(user_id=user.id, service_id=None, role=Role.VIEWER))
        await session.commit()
        return Account(user.id, email)

    async def remove(session: AsyncSession) -> None:
        await session.execute(delete(User).where(User.email == email))
        await session.commit()

    created = run_in_session(app_settings, create)
    try:
        yield created
    finally:
        run_in_session(app_settings, remove)


NO_LOGIN = "!"


def new_email() -> str:
    return f"new-{uuid.uuid4().hex[:10]}@example.test"


def make_user(settings: Settings, role: Role, *, two_factor: bool) -> User:
    async def run(session: AsyncSession) -> User:
        user = User(email=new_email(), password_hash=NO_LOGIN)
        session.add(user)
        await session.flush()
        session.add(UserRole(user_id=user.id, service_id=None, role=role))
        if two_factor:
            session.add(Totp(user_id=user.id, secret_ciphertext=b"x", enabled_at=datetime.now(UTC)))
        await session.commit()
        return user

    return run_in_session(settings, run)


@pytest.fixture
def as_user(app_settings: Settings) -> Iterator[Any]:
    """A client whose requests run as a given user, without a sign-in round trip."""
    clients: list[TestClient] = []

    def make(user: User) -> TestClient:
        client = make_client(app_settings)
        client.app.dependency_overrides[current_user] = lambda: user  # type: ignore[attr-defined]
        clients.append(client.__enter__())
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)
