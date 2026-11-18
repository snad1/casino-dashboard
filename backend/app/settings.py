"""Typed settings: plain values from the environment, secrets only from files.

Secrets are read from `SECRETS_DIR` (default `/run/secrets`, where compose mounts them),
one file per secret named after the field. The environment can never supply a secret, so
`docker inspect` and process listings stay clean. Loading fails fast on a missing, short,
placeholder or low-variety secret, and the error never contains the value.
"""

import os
import re
from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

AppEnv = Literal["local", "development", "testing", "staging", "production"]

DEFAULT_SECRETS_DIR = "/run/secrets"
MIN_SECRET_BYTES = 32
MIN_DISTINCT_CHARS = 8

SECRET_FIELDS = frozenset(
    {
        "session_secret",
        "totp_key",
        "postgres_app_password",
        "postgres_password",
        "redis_dashboard_password",
        "agent_token",
        "brevo_api_key",
        "smtp_password",
        "telegram_bot_token",
        "vapid_private_key",
    }
)
# Secrets we generate ourselves; third-party tokens have their own formats and lengths.
GENERATED_SECRETS = frozenset(
    {
        "session_secret",
        "totp_key",
        "postgres_password",
        "postgres_app_password",
        "redis_dashboard_password",
        "agent_token",
    }
)
DETAIL_ENVS = frozenset({"local", "development", "testing"})

_PLACEHOLDER = re.compile(
    r"(change.?me|replace.?me|your[-_ ]|<)"
    r"|(secret|password|passwd|example|test|default|admin|dummy|x+)$",
    re.IGNORECASE,
)
_HOSTNAME = re.compile(r"[A-Za-z0-9.-]+(:[0-9]{1,5})?")
_EMAIL = re.compile(r"[^@\s<>]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")


class _EnvWithoutSecrets(EnvSettingsSource):
    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        if field_name in SECRET_FIELDS:
            return None, field_name, False
        return super().get_field_value(field, field_name)


def weak_secret_reason(name: str, value: str) -> str | None:
    """Why `value` is not acceptable for secret `name`, or None when it is fine."""
    if not value.strip():
        return "is empty"
    if _PLACEHOLDER.match(value.strip()):
        return "is a placeholder or known default"
    if len(set(value)) < MIN_DISTINCT_CHARS:
        return "has too few distinct characters"
    if name in GENERATED_SECRETS and len(value.encode()) < MIN_SECRET_BYTES:
        return f"is shorter than {MIN_SECRET_BYTES} bytes"
    return None


class _SecretFileSettings(BaseSettings):
    """Plain values from the environment, secrets only from files, weak secrets refused."""

    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return init_settings, _EnvWithoutSecrets(settings_cls), file_secret_settings

    @model_validator(mode="after")
    def _reject_weak_secrets(self) -> Self:
        problems = []
        for name in sorted(SECRET_FIELDS & set(type(self).model_fields)):
            secret: SecretStr | None = getattr(self, name)
            if secret is None:
                continue
            if reason := weak_secret_reason(name, secret.get_secret_value()):
                problems.append(f"{name} {reason}")
        if problems:
            raise ValueError("; ".join(problems))
        return self


class DatabaseSettings(_SecretFileSettings):
    """What every process that talks to Postgres needs, and nothing more."""

    app_env: AppEnv
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "dashboard"
    postgres_user: str = "dashboard"
    postgres_password: SecretStr

    @property
    def exposes_error_detail(self) -> bool:
        """Whether error responses may carry exception detail (never in staging or production)."""
        return self.app_env in DETAIL_ENVS

    @property
    def database_url(self) -> str:
        password = quote(self.postgres_password.get_secret_value(), safe="")
        return (
            f"postgresql+asyncpg://{quote(self.postgres_user, safe='')}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


class MigrationSettings(DatabaseSettings):
    """Migrations run as the table owner and set the password of the api's own role."""

    postgres_app_password: SecretStr | None = None


class Settings(DatabaseSettings):
    """The api. It holds no mail credentials: only the mailer process sends mail."""

    public_host: str
    allowed_origins: list[str] = Field(default_factory=list)
    host_tz: str = "UTC"

    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_user: str = "dashboard"
    agent_socket: Path | None = None
    vapid_public_key: str | None = None

    session_secret: SecretStr
    totp_key: SecretStr
    redis_dashboard_password: SecretStr
    agent_token: SecretStr | None = None
    telegram_bot_token: SecretStr | None = None
    vapid_private_key: SecretStr | None = None

    @field_validator("public_host")
    @classmethod
    def _bare_hostname(cls, value: str) -> str:
        if not _HOSTNAME.fullmatch(value):
            raise ValueError("must be a bare hostname, without scheme or path")
        return value

    @field_validator("host_tz")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("is not a known time zone") from None
        return value

    @model_validator(mode="after")
    def _default_origins(self) -> Self:
        if not self.allowed_origins:
            self.allowed_origins = [f"https://{self.public_host}"]
        return self

    @property
    def public_origin(self) -> str:
        """The only base for links in emails; never taken from a request's Host header."""
        return f"https://{self.public_host}"

    @property
    def redis_url(self) -> str:
        password = quote(self.redis_dashboard_password.get_secret_value(), safe="")
        return (
            f"redis://{quote(self.redis_user, safe='')}:{password}"
            f"@{self.redis_host}:{self.redis_port}/0"
        )


class MailerSettings(DatabaseSettings):
    """The mailer process: reads the outbox and sends through Brevo or SMTP."""

    mail_backend: Literal["brevo", "smtp"]
    mail_from: str
    mail_from_name: str = "Casino dashboard"
    brevo_api_key: SecretStr | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None

    @field_validator("mail_from")
    @classmethod
    def _address(cls, value: str) -> str:
        if not _EMAIL.fullmatch(value):
            raise ValueError("must be a plain email address")
        return value

    @model_validator(mode="after")
    def _backend_is_configured(self) -> Self:
        if self.mail_backend == "brevo" and self.brevo_api_key is None:
            raise ValueError("brevo_api_key is required when mail_backend is brevo")
        if self.mail_backend == "smtp" and not (
            self.smtp_host and self.smtp_user and self.smtp_password
        ):
            raise ValueError("smtp_host, smtp_user and smtp_password are required for smtp")
        return self


def load_settings() -> Settings:
    """Load settings once at startup; raises `pydantic.ValidationError` naming bad fields."""
    return Settings(_secrets_dir=os.environ.get("SECRETS_DIR", DEFAULT_SECRETS_DIR))


def load_mailer_settings() -> MailerSettings:
    return MailerSettings(_secrets_dir=os.environ.get("SECRETS_DIR", DEFAULT_SECRETS_DIR))


def load_migration_settings() -> MigrationSettings:
    return MigrationSettings(_secrets_dir=os.environ.get("SECRETS_DIR", DEFAULT_SECRETS_DIR))
