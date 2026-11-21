"""Helpers for the Telegram user tests: bot link tokens, signed-in Telegram accounts, and each
test's Telegram users' history (fixtures in `tests/tg_rig.py`).

`sign` is the bot's own token rule (`wolfbet/tg/dashboard_link.py`); a test checks it against
the shared golden fixture, so the two never drift apart.
"""

import base64
import hashlib
import hmac
import json
import random
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.history.models import Bet, DbEpoch, HistorySession
from app.settings import Settings
from tests.conftest import make_client, run_in_session

GOLDEN = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "tg_login_v1"
SECRET = secrets.token_urlsafe(48)
SERVICE = "wolfbet-tg"
T0 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign(secret: str, uid: int, link_id: str, exp: int) -> str:
    payload = json.dumps(
        {"exp": exp, "id": link_id, "uid": uid, "v": 1}, sort_keys=True, separators=(",", ":")
    )
    body = _b64(payload.encode())
    return f"{body}.{_b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())}"


def token(uid: int, *, secret: str = SECRET, exp: int | None = None) -> str:
    return sign(secret, uid, secrets.token_urlsafe(16), exp or int(time.time()) + 300)


def new_uid() -> int:
    return random.randint(10**9, 2 * 10**9)  # noqa: S311  # an id, not a secret


class TgUsers:
    """Creates Telegram users with history and signed-in clients; removes them afterwards."""

    def __init__(self, settings: Settings, owner_settings: Settings) -> None:
        self.settings = settings
        self.owner_settings = owner_settings
        self.uids: list[int] = []
        self.clients: list[TestClient] = []

    def client(self, uid: int) -> TestClient:
        """A browser signed in through a fresh bot link for `uid`."""
        self.uids.append(uid)
        client = make_client(self.settings).__enter__()
        self.clients.append(client)
        assert client.post("/auth/telegram", json={"token": token(uid)}).status_code == 204
        return client

    def history(self, uid: int, sessions: int = 2, bets: int = 3) -> int:
        """`sessions` sessions of `bets` bets each in a new database of `uid`; its epoch id."""
        self.uids.append(uid)

        async def add(db: AsyncSession) -> int:
            epoch = DbEpoch(
                service=SERVICE,
                source_path=f"/botdata/.wolfbot_tg/{uid}/wolfbet.db",
                inode=uid,
                first_started_at=T0,
                tg_uid=uid,
            )
            db.add(epoch)
            await db.flush()
            for n in range(1, sessions + 1):
                db.add(
                    HistorySession(
                        service=SERVICE,
                        epoch_id=epoch.epoch_id,
                        src_id=n,
                        lane=1,
                        started_at=T0 + timedelta(hours=n),
                        ended_at=T0 + timedelta(hours=n, minutes=5),
                        currency="btc",
                        strategy="=cmd" if n == 1 else "Martingale",
                        total_bets=bets,
                        profit=0.5 * n,
                    )
                )
                for b in range(bets):
                    db.add(
                        Bet(
                            service=SERVICE,
                            epoch_id=epoch.epoch_id,
                            src_id=n * 100 + b,
                            ts=T0 + timedelta(hours=n, seconds=b),
                            session_src_id=n,
                            lane=1,
                            amount=1.0,
                            state="win",
                            profit=0.1,
                            balance_after=100.0 + b,
                        )
                    )
            await db.commit()
            return epoch.epoch_id

        return run_in_session(self.owner_settings, add)

    def close(self) -> None:
        for client in self.clients:
            client.__exit__(None, None, None)

        async def remove(db: AsyncSession) -> None:
            epochs = select(DbEpoch.epoch_id).where(DbEpoch.tg_uid.in_(self.uids))
            for table in (Bet, HistorySession):
                await db.execute(
                    delete(table).where(table.service == SERVICE, table.epoch_id.in_(epochs))
                )
            await db.execute(delete(DbEpoch).where(DbEpoch.tg_uid.in_(self.uids)))
            await db.execute(delete(User).where(User.tg_uid.in_(self.uids)))
            await db.commit()

        run_in_session(self.owner_settings, remove)


def golden() -> dict[str, Any]:
    case: dict[str, Any] = json.loads((GOLDEN / "token.json").read_text())
    return case
