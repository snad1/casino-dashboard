"""Every test gets valid secret files in a temporary directory and a testing environment."""

import secrets
from collections.abc import Iterator
from pathlib import Path

import pytest

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
