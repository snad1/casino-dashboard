"""Users, per-service roles, and the credential tables the login, 2FA and invite flows use."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from ipaddress import IPv4Address, IPv6Address

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, INET
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"
    TG_USER = "tg_user"
    LANE_MANAGER = "lane_manager"


_ROLE_CHECK = "role IN (" + ", ".join(f"'{role}'" for role in Role) + ")"


def _id() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(always=True), primary_key=True)


def _user_fk(*, index: bool = False, primary_key: bool = False) -> Mapped[int]:
    return mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=index,
        primary_key=primary_key,
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    """A dashboard account: an invited email, or a Telegram user (`tg_uid`) signed in through
    the bot's one-time link, who has neither email nor password."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("email IS NOT NULL OR tg_uid IS NOT NULL", name="email_or_tg_uid"),
        CheckConstraint("lane_quota BETWEEN 1 AND 99", name="lane_quota"),
    )

    id: Mapped[int] = _id()
    email: Mapped[str | None] = mapped_column(CITEXT, unique=True)
    tg_uid: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    disabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    failed_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    lane_quota: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    created_at: Mapped[datetime] = _created_at()


@dataclass(frozen=True)
class AuditActor:
    """Who made the request, copied while the user row is still loaded: the audit row is
    written after the request's session has closed, and a rollback expires the ORM row."""

    id: int
    email: str | None

    @classmethod
    def of(cls, user: User) -> "AuditActor":
        return cls(user.id, user.email)


LANE_CHECK = "lane IS NULL OR (service_id IS NOT NULL AND lane BETWEEN 1 AND 99)"
INVITE_LANES_MAX = 20
INVITE_LANES_CHECK = (
    "cardinality(lanes) = 0 OR (service_id IS NOT NULL AND role IN ('viewer', 'operator')"
    f" AND cardinality(lanes) <= {INVITE_LANES_MAX} AND 1 <= ALL(lanes) AND 99 >= ALL(lanes))"
)


class UserRole(Base):
    """A role on one service, or on every service when `service_id` is null.

    `lane` narrows a service role to one lane. Such a role never counts for a service-wide
    check: it grants only what a check that names that lane allows.
    """

    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "service_id", "lane", postgresql_nulls_not_distinct=True),
        CheckConstraint(_ROLE_CHECK, name="role"),
        CheckConstraint(LANE_CHECK, name="lane"),
        # The database, not only the CLI, guarantees there is never a second owner.
        Index(
            "uq_user_roles_single_owner",
            "role",
            unique=True,
            postgresql_where=text("role = 'owner'"),
        ),
    )

    id: Mapped[int] = _id()
    user_id: Mapped[int] = _user_fk()
    service_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("services.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(Text)
    lane: Mapped[int | None] = mapped_column(SmallInteger)


class ApiSession(Base):
    """Expiry is derived: absolute from `created_at`, idle from `last_seen_at`."""

    __tablename__ = "api_sessions"

    id_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    user_id: Mapped[int] = _user_fk(index=True)
    ip: Mapped[IPv4Address | IPv6Address | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    step_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invite(Base):
    __tablename__ = "invites"
    __table_args__ = (
        CheckConstraint(_ROLE_CHECK, name="role"),
        CheckConstraint(INVITE_LANES_CHECK, name="lanes"),
    )

    id: Mapped[int] = _id()
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    email: Mapped[str] = mapped_column(CITEXT)
    role: Mapped[str] = mapped_column(Text)
    service_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("services.id", ondelete="CASCADE")
    )
    lanes: Mapped[list[int]] = mapped_column(
        ARRAY(SmallInteger), server_default=text("'{}'"), default=list
    )
    invited_by: Mapped[int] = _user_fk()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class TgLoginToken(Base):
    """A bot login link that was already used; its id is kept hashed."""

    __tablename__ = "tg_login_tokens"

    id_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PasswordReset(Base):
    __tablename__ = "password_resets"

    id: Mapped[int] = _id()
    user_id: Mapped[int] = _user_fk(index=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class Totp(Base):
    """Enrolment counts only once `enabled_at` is set; `last_counter` blocks code replay."""

    __tablename__ = "totp"

    user_id: Mapped[int] = _user_fk(primary_key=True)
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_counter: Mapped[int | None] = mapped_column(BigInteger)


class RecoveryCode(Base):
    __tablename__ = "recovery_codes"

    id: Mapped[int] = _id()
    user_id: Mapped[int] = _user_fk(index=True)
    code_hash: Mapped[str] = mapped_column(Text)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
