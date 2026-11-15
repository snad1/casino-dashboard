"""Settings refuse missing or weak secrets and never echo a secret value."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.settings import load_settings
from tests.conftest import PUBLIC_HOST, REQUIRED_SECRETS

STRONG = "Zq3v-Lk9P_x2RtY7mWb4NcH8sJd6Fa1Ge5Uo0Ii"


def _fails_naming(field: str, value: str | None = None) -> None:
    with pytest.raises(ValidationError) as caught:
        load_settings()
    text = str(caught.value)
    assert field in text
    if value:
        assert value not in text


def test_valid_secrets_load(secrets_dir: Path) -> None:
    settings = load_settings()
    assert settings.app_env == "testing"
    assert settings.allowed_origins == [f"https://{PUBLIC_HOST}"]
    assert settings.agent_token is None
    assert "**" in repr(settings.session_secret)


@pytest.mark.parametrize("name", REQUIRED_SECRETS)
def test_missing_required_secret_stops_startup(secrets_dir: Path, name: str) -> None:
    (secrets_dir / name).unlink()
    _fails_naming(name)


@pytest.mark.parametrize(
    "value",
    [
        "short-but-random-Q8w",
        "change-me-to-a-random-64-char-string",
        "CHANGEME",
        "password",
        "your-secret-here-0123456789-abcdefghijklmnop",
        "<generate with openssl rand -base64 36>",
        "ab" * 40,
        "   ",
    ],
)
def test_weak_session_secret_stops_startup(secrets_dir: Path, value: str) -> None:
    (secrets_dir / "session_secret").write_text(value)
    _fails_naming("session_secret", value.strip() or None)


def test_secret_in_environment_is_ignored(
    secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (secrets_dir / "session_secret").unlink()
    monkeypatch.setenv("SESSION_SECRET", STRONG)
    _fails_naming("session_secret", STRONG)


def test_optional_secret_is_checked_when_set(secrets_dir: Path) -> None:
    (secrets_dir / "telegram_bot_token").write_text("changeme")
    _fails_naming("telegram_bot_token", "changeme")


def test_optional_third_party_token_has_no_length_floor(secrets_dir: Path) -> None:
    (secrets_dir / "brevo_api_key").write_text("xkeysib-9f8e7d6c5b4a")
    assert load_settings().brevo_api_key is not None


@pytest.mark.parametrize("host", ["https://dash.example.test", "dash.test/x", "a b", "h\r\nX: y"])
def test_public_host_must_be_bare(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    monkeypatch.setenv("PUBLIC_HOST", host)
    _fails_naming("public_host")


def test_unknown_time_zone_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_TZ", "Mars/Olympus")
    _fails_naming("host_tz")


def test_unknown_app_env_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    _fails_naming("app_env")


def test_urls_are_built_from_parts_with_quoted_password(
    secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (secrets_dir / "postgres_password").write_text("p@ss/word:with#odd+chars?" + STRONG)
    monkeypatch.setenv("POSTGRES_HOST", "timescale")
    monkeypatch.setenv("REDIS_HOST", "redis")
    settings = load_settings()
    assert settings.database_url.startswith("postgresql+asyncpg://dashboard:p%40ss%2Fword%3A")
    assert settings.database_url.endswith("@timescale:5432/dashboard")
    assert settings.redis_url.startswith("redis://dashboard:")
    assert settings.redis_url.endswith("@redis:6379/0")


def test_error_detail_only_outside_staging_and_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = [("testing", True), ("local", True), ("staging", False), ("production", False)]
    for env, exposed in cases:
        monkeypatch.setenv("APP_ENV", env)
        assert load_settings().exposes_error_detail is exposed
