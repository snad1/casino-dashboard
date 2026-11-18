"""create-owner makes exactly one owner and refuses weak or mismatched passwords."""

from collections.abc import Iterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import cli
from app.auth.models import Role, User, UserRole

STRONG = "tq8#Lm2v!Rw9zK-orbit-lantern"
# Not an argon2 hash, so no password can ever match it.
NO_LOGIN = "!"


def _prompts(*answers: str) -> Iterator[str]:
    return iter(answers)


def test_read_password_accepts_a_strong_matching_password() -> None:
    answers = _prompts(STRONG, STRONG)
    assert cli.read_password("me@example.test", lambda _: next(answers)) == STRONG


@pytest.mark.parametrize(
    ("first", "second", "message"),
    [
        ("short1!", "short1!", "at least 12 characters"),
        ("Password123!", "Password123!", "too easy to guess"),
        ("me@example.test1", "me@example.test1", "too easy to guess"),
        (STRONG, STRONG + "x", "do not match"),
    ],
)
def test_read_password_refuses(first: str, second: str, message: str) -> None:
    answers = _prompts(first, second)
    with pytest.raises(cli.OwnerError, match=message):
        cli.read_password("me@example.test", lambda _: next(answers))


def test_main_reports_a_refusal_and_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = _prompts(STRONG, "different-but-long")
    monkeypatch.setattr("getpass.getpass", lambda _: next(answers))
    assert cli.main(["create-owner", "--email", "me@example.test"]) == 1
    assert "Passwords do not match." in capsys.readouterr().err


def test_main_rejects_a_bad_email(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["create-owner", "--email", "not-an-email"])
    assert exit_info.value.code == 2
    assert "is not an email address" in capsys.readouterr().err


async def test_create_owner_stores_an_argon2id_hash_and_a_global_owner_role(
    db_session: AsyncSession,
) -> None:
    user = await cli.create_owner(db_session, "Owner@Example.test", STRONG)
    assert user.password_hash.startswith("$argon2id$")
    assert STRONG not in user.password_hash
    role = await db_session.scalar(select(UserRole).where(UserRole.user_id == user.id))
    assert role is not None
    assert (role.role, role.service_id) == (Role.OWNER, None)


async def test_create_owner_refuses_a_second_owner(db_session: AsyncSession) -> None:
    await cli.create_owner(db_session, "first@example.test", STRONG)
    with pytest.raises(cli.OwnerError, match="An owner already exists"):
        await cli.create_owner(db_session, "second@example.test", STRONG)
    emails = ["first@example.test", "second@example.test"]
    query = select(func.count()).select_from(User).where(User.email.in_(emails))
    assert await db_session.scalar(query) == 1


async def test_database_refuses_a_second_owner_row(db_session: AsyncSession) -> None:
    first = User(email="a@example.test", password_hash=NO_LOGIN)
    second = User(email="b@example.test", password_hash=NO_LOGIN)
    db_session.add_all([first, second])
    await db_session.flush()
    db_session.add(UserRole(user_id=first.id, role=Role.OWNER))
    await db_session.flush()
    db_session.add(UserRole(user_id=second.id, role=Role.OWNER))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_email_is_unique_regardless_of_case(db_session: AsyncSession) -> None:
    db_session.add(User(email="Same@Example.test", password_hash=NO_LOGIN))
    await db_session.flush()
    db_session.add(User(email="same@example.TEST", password_hash=NO_LOGIN))
    with pytest.raises(IntegrityError):
        await db_session.flush()
