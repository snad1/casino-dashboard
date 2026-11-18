"""Operator commands, run as `python -m app.cli <command>`.

`create-owner` makes the first and only owner. There is no default user: until this runs,
nobody can sign in.
"""

import argparse
import asyncio
import getpass
import sys
from collections.abc import Callable, Sequence

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import verify_chain
from app.auth.models import Role, User, UserRole
from app.auth.passwords import hash_password, password_problem
from app.db.engine import create_engine, create_sessionmaker
from app.settings import load_settings

OWNER_EXISTS = "An owner already exists."
EMAIL_TAKEN = "That email already has an account."


class OwnerError(Exception):
    """The owner cannot be created; the message is safe to print."""


async def create_owner(session: AsyncSession, email: str, password: str) -> User:
    """Insert the owner with a global owner role, refusing when one already exists."""
    if await session.scalar(select(exists().where(UserRole.role == Role.OWNER))):
        raise OwnerError(OWNER_EXISTS)
    user = User(email=email, password_hash=hash_password(password))
    session.add(user)
    try:
        await session.flush()
        session.add(UserRole(user_id=user.id, service_id=None, role=Role.OWNER))
        await session.flush()
    except IntegrityError:
        # A concurrent run won the race, or the email is already used by another account.
        await session.rollback()
        if await session.scalar(select(exists().where(UserRole.role == Role.OWNER))):
            raise OwnerError(OWNER_EXISTS) from None
        raise OwnerError(EMAIL_TAKEN) from None
    await session.commit()
    return user


def read_password(email: str, prompt: Callable[[str], str]) -> str:
    password = prompt("Password: ")
    if problem := password_problem(password, email):
        raise OwnerError(problem)
    if prompt("Repeat password: ") != password:
        raise OwnerError("Passwords do not match.")
    return password


async def _create_owner_in_db(email: str, password: str) -> None:
    engine = create_engine(load_settings())
    try:
        async with create_sessionmaker(engine)() as session:
            await create_owner(session, email, password)
    finally:
        await engine.dispose()


async def _verify_audit() -> int | None:
    engine = create_engine(load_settings())
    try:
        async with create_sessionmaker(engine)() as session:
            return await verify_chain(session)
    finally:
        await engine.dispose()


def _valid_email(value: str) -> str:
    local, _, domain = value.strip().rpartition("@")
    if not local or "." not in domain or any(c.isspace() for c in value.strip()):
        raise argparse.ArgumentTypeError("is not an email address")
    return value.strip()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    owner = commands.add_parser("create-owner", help="create the first owner (once)")
    owner.add_argument("--email", required=True, type=_valid_email)
    commands.add_parser("verify-audit", help="check the audit log hash chain; exit 1 if broken")
    args = parser.parse_args(argv)

    if args.command == "verify-audit":
        broken = asyncio.run(_verify_audit())
        if broken is not None:
            print(f"Audit log chain broken at id {broken}.", file=sys.stderr)
            return 1
        print("Audit log chain intact.")
        return 0
    try:
        password = read_password(args.email, getpass.getpass)
        asyncio.run(_create_owner_in_db(args.email, password))
    except OwnerError as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Owner created: {args.email}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
