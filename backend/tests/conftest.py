"""Every test gets valid secret files and a testing environment; DB tests get real Timescale."""

import asyncio
import hashlib
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path

import pytest
import yaml
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from app.settings import Settings, load_settings

REQUIRED_SECRETS = ("session_secret", "postgres_password", "redis_dashboard_password")
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
            _secrets_dir=str(directory),
        )


def alembic_config(settings: Settings) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.attributes["url"] = settings.database_url
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


@pytest.fixture(scope="session")
def app_settings(
    migrated: Settings, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Settings]:
    """Migrated Timescale plus a Redis running the deployed ACL, as the api connects to both."""
    dashboard_password = secrets.token_urlsafe(48)
    acl_dir = tmp_path_factory.mktemp("redis-acl")
    (acl_dir / "users.acl").write_text(
        ACL_TEMPLATE.read_text()
        .replace("PUBLISHER_HASH", _sha256(secrets.token_urlsafe(48)))
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
        directory = tmp_path_factory.mktemp("app-secrets")
        for name in REQUIRED_SECRETS:
            (directory / name).write_text(secrets.token_urlsafe(48))
        (directory / "postgres_password").write_text(migrated.postgres_password.get_secret_value())
        (directory / "redis_dashboard_password").write_text(dashboard_password)
        yield Settings(
            app_env="testing",
            public_host=PUBLIC_HOST,
            postgres_host=migrated.postgres_host,
            postgres_port=migrated.postgres_port,
            redis_host=container.get_container_host_ip(),
            redis_port=int(container.get_exposed_port(6379)),
            _secrets_dir=str(directory),
        )
