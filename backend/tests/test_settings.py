"""Settings refuse missing or weak secrets and never echo a secret value."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.settings import load_ingest_settings, load_mailer_settings, load_settings
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
    (secrets_dir / "agent_token").write_text("changeme")
    _fails_naming("agent_token", "changeme")


def _mailer_env(monkeypatch: pytest.MonkeyPatch, backend: str) -> None:
    monkeypatch.setenv("MAIL_BACKEND", backend)
    monkeypatch.setenv("MAIL_FROM", "no-reply@example.test")
    monkeypatch.setenv("HOST_TZ", "Europe/Paris")


def test_the_mailer_holds_the_telegram_token_and_an_empty_file_keeps_it_off(
    secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (secrets_dir / "brevo_api_key").write_text("xkeysib-9f8e7d6c5b4a")
    _mailer_env(monkeypatch, "brevo")
    (secrets_dir / "telegram_bot_token").write_text("\n")
    assert load_mailer_settings().telegram_bot_token is None
    (secrets_dir / "telegram_bot_token").write_text("123456789:AAH-telegram-token-x9Yz")
    assert load_mailer_settings().telegram_bot_token is not None
    (secrets_dir / "telegram_bot_token").write_text("changeme")
    with pytest.raises(ValidationError, match="telegram_bot_token"):
        load_mailer_settings()
    assert not hasattr(load_settings(), "telegram_bot_token")


def test_the_mailer_needs_the_host_time_zone(
    secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (secrets_dir / "brevo_api_key").write_text("xkeysib-9f8e7d6c5b4a")
    _mailer_env(monkeypatch, "brevo")
    monkeypatch.delenv("HOST_TZ")
    with pytest.raises(ValidationError, match="host_tz"):
        load_mailer_settings()


def test_third_party_mail_key_has_no_length_floor(
    secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (secrets_dir / "brevo_api_key").write_text("xkeysib-9f8e7d6c5b4a")
    _mailer_env(monkeypatch, "brevo")
    assert load_mailer_settings().brevo_api_key is not None


def test_the_api_never_loads_mail_credentials(secrets_dir: Path) -> None:
    (secrets_dir / "brevo_api_key").write_text("xkeysib-9f8e7d6c5b4a")
    assert not hasattr(load_settings(), "brevo_api_key")


@pytest.mark.parametrize(("backend", "field"), [("brevo", "brevo_api_key"), ("smtp", "smtp_host")])
def test_mailer_refuses_a_half_configured_backend(
    secrets_dir: Path, monkeypatch: pytest.MonkeyPatch, backend: str, field: str
) -> None:
    _mailer_env(monkeypatch, backend)
    with pytest.raises(ValidationError, match=field):
        load_mailer_settings()


def test_mail_key_comes_only_from_a_file(
    secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mailer_env(monkeypatch, "brevo")
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-from-the-environment")
    with pytest.raises(ValidationError, match="brevo_api_key"):
        load_mailer_settings()


@pytest.mark.parametrize("host", ["https://dash.example.test", "dash.test/x", "a b", "h\r\nX: y"])
def test_public_host_must_be_bare(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    monkeypatch.setenv("PUBLIC_HOST", host)
    _fails_naming("public_host")


def test_unknown_time_zone_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_TZ", "Mars/Olympus")
    _fails_naming("host_tz")


def test_ingest_needs_a_known_host_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOST_TZ", raising=False)
    with pytest.raises(ValidationError, match="host_tz"):
        load_ingest_settings()
    monkeypatch.setenv("HOST_TZ", "Mars/Olympus")
    with pytest.raises(ValidationError, match="host_tz"):
        load_ingest_settings()
    monkeypatch.setenv("HOST_TZ", "Europe/Berlin")
    assert load_ingest_settings().host_tz == "Europe/Berlin"


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


def test_the_alert_bot_username_is_checked_and_blank_means_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "@casino_alert_bot")
    assert load_settings().telegram_bot_username == "casino_alert_bot"
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", " ")
    assert load_settings().telegram_bot_username is None
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "bad name/x")
    with pytest.raises(ValidationError, match="telegram_bot_username"):
        load_settings()


def _vapid_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    return (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )


def test_the_push_key_lives_in_the_mailer_and_must_be_a_p256_pem(
    secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (secrets_dir / "brevo_api_key").write_text("xkeysib-9f8e7d6c5b4a")
    _mailer_env(monkeypatch, "brevo")
    (secrets_dir / "vapid_private_key").write_text("")
    assert load_mailer_settings().vapid_private_key is None
    (secrets_dir / "vapid_private_key").write_text(_vapid_pem())
    assert load_mailer_settings().vapid_private_key is not None
    (secrets_dir / "vapid_private_key").write_text("-----BEGIN PRIVATE KEY-----\nnot a key x9\n")
    with pytest.raises(ValidationError, match="vapid_private_key"):
        load_mailer_settings()
    assert not hasattr(load_settings(), "vapid_private_key")


def test_the_api_takes_only_a_well_formed_public_push_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "B" + "x" * 86)
    assert load_settings().vapid_public_key == "B" + "x" * 86
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "")
    assert load_settings().vapid_public_key is None
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "not-a-key")
    with pytest.raises(ValidationError, match="vapid_public_key"):
        load_settings()
