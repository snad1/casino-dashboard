"""The deployed Redis entrypoint: every served bot but WolfBet publishes as its own user, may
append only to its own streams, and the dashboard reads them all; a missing password or an
unsafe service name stops Redis before it starts."""

import secrets
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from redis import Redis
from redis.exceptions import AuthenticationError, NoPermissionError
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from tests.conftest import COMPOSE_FILE, REDIS_SERVICE, compose_image

REDIS_DIR = COMPOSE_FILE.parent / "redis"


def _write(directory: Path, name: str, value: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o755)
    (directory / name).write_text(value)
    (directory / name).chmod(0o644)


def _container(
    tmp_path: Path, services: str, demo: str | None
) -> tuple[DockerContainer, dict[str, str]]:
    passwords = {
        "publisher": secrets.token_urlsafe(32),
        "dashboard": secrets.token_urlsafe(32),
    }
    _write(tmp_path / "secrets", "redis_publisher_password", passwords["publisher"])
    _write(tmp_path / "secrets", "redis_dashboard_password", passwords["dashboard"])
    (tmp_path / "publishers").mkdir()
    (tmp_path / "publishers").chmod(0o755)
    if demo is not None:
        _write(tmp_path / "publishers", "demo.txt", demo)
        passwords["demo-pub"] = demo
    container = (
        DockerContainer(compose_image(REDIS_SERVICE))
        .with_exposed_ports(6379)
        .with_env("DASHBOARD_SERVICES", services)
        .with_volume_mapping(str(REDIS_DIR), "/etc/redis", "ro")
        .with_volume_mapping(str(tmp_path / "secrets"), "/run/secrets", "ro")
        .with_volume_mapping(str(tmp_path / "publishers"), "/run/publishers", "ro")
        .with_command("/bin/sh /etc/redis/entrypoint.sh")
    )
    return container, passwords


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[Any, dict[str, str]]]:
    container, passwords = _container(tmp_path, "wolfbet,demo", secrets.token_urlsafe(32))
    container.waiting_for(LogMessageWaitStrategy("Ready to accept connections"))
    with container:
        host, port = container.get_container_host_ip(), int(container.get_exposed_port(6379))

        def client(user: str) -> Redis:
            return Redis(host=host, port=port, username=user, password=passwords[user])

        yield client, passwords


def test_each_publisher_appends_only_to_its_own_streams(served: tuple[Any, dict[str, str]]) -> None:
    client, _ = served
    assert client("demo-pub").xadd("demo:bets", {"data": "{}"})
    assert client("publisher").xadd("wolfbet:bets", {"data": "{}"})
    with pytest.raises(NoPermissionError):
        client("demo-pub").xadd("wolfbet:bets", {"data": "{}"})
    with pytest.raises(NoPermissionError):
        client("publisher").xadd("demo:bets", {"data": "{}"})


def test_the_dashboard_reads_every_service_and_writes_none(
    served: tuple[Any, dict[str, str]],
) -> None:
    client, _ = served
    client("demo-pub").xadd("demo:bets", {"data": "{}"})
    client("publisher").xadd("wolfbet:bets", {"data": "{}"})
    dashboard = client("dashboard")
    assert len(dashboard.xrange("demo:bets")) == 1
    assert len(dashboard.xrange("wolfbet:bets")) == 1
    with pytest.raises(NoPermissionError):
        dashboard.xadd("demo:bets", {"data": "{}"})


def _refused(tmp_path: Path, services: str, demo: str | None) -> str:
    container, passwords = _container(tmp_path, services, demo)
    with container:
        wrapped = container.get_wrapped_container()
        assert wrapped.wait(timeout=30)["StatusCode"] != 0
        logs: str = wrapped.logs().decode()
    for value in passwords.values():
        assert value not in logs
    return logs


def test_a_missing_publisher_password_stops_redis(tmp_path: Path) -> None:
    logs = _refused(tmp_path, "wolfbet,demo", None)
    assert "publisher password for demo is missing or empty; refusing to start" in logs


def test_an_unsafe_service_name_stops_redis(tmp_path: Path) -> None:
    logs = _refused(tmp_path, "wolfbet,demo;x", secrets.token_urlsafe(32))
    assert "service names are a-z, 0-9 and -; refusing to start" in logs


def test_wolfbet_alone_keeps_todays_users(tmp_path: Path) -> None:
    container, passwords = _container(tmp_path, "wolfbet", secrets.token_urlsafe(32))
    container.waiting_for(LogMessageWaitStrategy("Ready to accept connections"))
    with container:
        host, port = container.get_container_host_ip(), int(container.get_exposed_port(6379))
        publisher = Redis(
            host=host, port=port, username="publisher", password=passwords["publisher"]
        )
        assert publisher.xadd("wolfbet:bets", {"data": "{}"})
        stranger = Redis(host=host, port=port, username="demo-pub", password=passwords["demo-pub"])
        with pytest.raises(AuthenticationError):
            stranger.ping()
