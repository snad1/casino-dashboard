"""Seed a throwaway e2e stack: one viewer, the wolfbet service, some past sessions, and two
Telegram users' own history.

Runs inside the api image with the stack's secrets (`python /tools/e2e_seed.py`). The viewer
signs in with `E2E_EMAIL` / `E2E_PASSWORD`; `E2E_VIEWERS=N` adds viewer1..N@e2e.test with the
same password, for load runs that need many accounts. Past sessions and bets go straight into the
history tables, so the sessions list, chart history and session page have data without a bot
database. Telegram users `TG_USERS` each get their own database epoch (service `wolfbet-tg`)
with a few sessions, so their pages can be checked against each other; they sign in with a
link signed by the stack's `tg_login_secret`. It refuses to run unless APP_ENV is `testing` or
`staging`, and it is idempotent.
"""

import asyncio
import os
import random
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Role, User, UserRole
from app.auth.passwords import hash_password
from app.db.engine import create_engine, create_sessionmaker
from app.db.models import Service
from app.history.models import Bet, DbEpoch, HistorySession
from app.settings import DEFAULT_SECRETS_DIR, DatabaseSettings

SERVICE = "wolfbet"
SESSIONS = 6
BETS_PER_SESSION = 240
ALLOWED_ENVS = ("testing", "staging")
TG_SERVICE = "wolfbet-tg"
TG_USERS = {7001: 2, 7002: 3}
TG_BETS = 5


async def seed_tg_users(db: AsyncSession, now: datetime) -> None:
    """Each Telegram user's sessions in a database of their own, once."""
    for uid, sessions in TG_USERS.items():
        exists = select(DbEpoch.epoch_id).where(
            DbEpoch.service == TG_SERVICE, DbEpoch.tg_uid == uid
        )
        if await db.scalar(exists) is not None:
            continue
        first = now - timedelta(hours=sessions)
        epoch = DbEpoch(
            service=TG_SERVICE,
            source_path=f"/botdata/.wolfbot_tg/{uid}/wolfbet.db",
            inode=uid,
            first_started_at=first,
            tg_uid=uid,
        )
        db.add(epoch)
        await db.flush()
        for n in range(1, sessions + 1):
            started = first + timedelta(hours=n - 1)
            for i in range(1, TG_BETS + 1):
                db.add(
                    Bet(
                        service=TG_SERVICE,
                        epoch_id=epoch.epoch_id,
                        src_id=n * 100 + i,
                        ts=started + timedelta(seconds=i),
                        session_src_id=n,
                        lane=1,
                        amount=0.00001,
                        multiplier=2.0,
                        rule="over",
                        state="win",
                        profit=0.00001,
                        balance_after=0.01 + i * 0.00001,
                    )
                )
            db.add(
                HistorySession(
                    service=TG_SERVICE,
                    epoch_id=epoch.epoch_id,
                    src_id=n,
                    lane=1,
                    started_at=started,
                    ended_at=started + timedelta(minutes=5),
                    currency="btc",
                    strategy=f"tg-{uid}",
                    total_bets=TG_BETS,
                    wins=TG_BETS,
                    losses=0,
                    profit=round(TG_BETS * 0.00001, 8),
                )
            )


async def seed(email: str, password: str, extra_viewers: int = 0) -> None:
    settings = DatabaseSettings(_secrets_dir=os.environ.get("SECRETS_DIR", DEFAULT_SECRETS_DIR))
    if settings.app_env not in ALLOWED_ENVS:
        raise SystemExit(f"refusing to seed: APP_ENV is {settings.app_env}")
    engine = create_engine(settings)
    rng = random.Random(7)  # noqa: S311  # repeatable test data, not a secret
    now = datetime.now(UTC).replace(microsecond=0)
    try:
        async with create_sessionmaker(engine)() as db:
            emails = [email, *(f"viewer{n}@e2e.test" for n in range(1, extra_viewers + 1))]
            password_hash = hash_password(password)
            for address in emails:
                if await db.scalar(select(User.id).where(User.email == address)) is None:
                    user = User(email=address, password_hash=password_hash)
                    db.add(user)
                    await db.flush()
                    db.add(UserRole(user_id=user.id, service_id=None, role=Role.VIEWER))
            if await db.scalar(select(Service.id).where(Service.name == SERVICE)) is None:
                db.add(Service(name=SERVICE, adapter=SERVICE, host="localhost"))
            await seed_tg_users(db, now)
            if await db.scalar(select(DbEpoch.epoch_id).where(DbEpoch.service == SERVICE)):
                await db.commit()
                return
            first = now - timedelta(hours=SESSIONS)
            epoch = DbEpoch(
                service=SERVICE, source_path="/botdata/e2e.db", inode=1, first_started_at=first
            )
            db.add(epoch)
            await db.flush()
            for n in range(1, SESSIONS + 1):
                started = first + timedelta(hours=n - 1)
                balance, profit, wins = 0.01, 0.0, 0
                for i in range(1, BETS_PER_SESSION + 1):
                    win = rng.random() < 0.5
                    step = 0.00001 if win else -0.00001
                    balance, profit, wins = balance + step, profit + step, wins + win
                    db.add(
                        Bet(
                            service=SERVICE,
                            epoch_id=epoch.epoch_id,
                            src_id=(n - 1) * BETS_PER_SESSION + i,
                            ts=started + timedelta(seconds=i * 10),
                            session_src_id=n,
                            lane=(n - 1) % 3 + 1,
                            amount=0.00001,
                            multiplier=2.0,
                            rule="over",
                            bet_value=49.5,
                            result_value=75.0 if win else 20.0,
                            state="win" if win else "loss",
                            profit=step,
                            balance_after=round(balance, 8),
                        )
                    )
                db.add(
                    HistorySession(
                        service=SERVICE,
                        epoch_id=epoch.epoch_id,
                        src_id=n,
                        lane=(n - 1) % 3 + 1,
                        started_at=started,
                        ended_at=started + timedelta(seconds=BETS_PER_SESSION * 10),
                        currency="btc",
                        strategy="martingale",
                        total_bets=BETS_PER_SESSION,
                        wins=wins,
                        losses=BETS_PER_SESSION - wins,
                        profit=round(profit, 8),
                        wagered=round(BETS_PER_SESSION * 0.00001, 8),
                        start_balance=0.01,
                        end_balance=round(balance, 8),
                        max_win_streak=4,
                        max_loss_streak=4,
                    )
                )
            await db.commit()
    finally:
        await engine.dispose()


def main() -> int:
    email, password = os.environ.get("E2E_EMAIL"), os.environ.get("E2E_PASSWORD")
    if not email or not password:
        print("set E2E_EMAIL and E2E_PASSWORD", file=sys.stderr)
        return 2
    extra = os.environ.get("E2E_VIEWERS", "0")
    if not extra.isdigit():
        print("E2E_VIEWERS must be a number", file=sys.stderr)
        return 2
    asyncio.run(seed(email, password, int(extra)))
    print(f"seeded {SERVICE} for {email}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
