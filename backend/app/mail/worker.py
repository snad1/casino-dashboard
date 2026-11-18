"""The mailer process: drains the outbox, one row per transaction, with backoff on failure.

It runs as its own container, the only one with a route to the internet, so the api never
needs outbound access. Rows are claimed with `FOR UPDATE SKIP LOCKED`, so a second worker
would never send the same message twice.
"""

import asyncio
import logging
import signal
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.engine import create_engine, create_sessionmaker
from app.mail.mailers import Mailer, MailError, Message, mailer_from
from app.mail.models import DEAD, PENDING, SENT, OutboxMessage
from app.settings import load_mailer_settings

HEARTBEAT = Path("/tmp/mailer.alive")  # noqa: S108  # nosec B108: private tmpfs in the container
POLL_SECONDS = 5.0
MAX_ATTEMPTS = 8
BASE_DELAY = timedelta(seconds=30)
MAX_DELAY = timedelta(hours=1)

logger = logging.getLogger("mailer")


def retry_delay(attempts: int) -> timedelta:
    delay: timedelta = BASE_DELAY * (2 ** (attempts - 1))
    return min(delay, MAX_DELAY)


async def process_once(sessionmaker: async_sessionmaker[AsyncSession], mailer: Mailer) -> bool:
    """Send the oldest due message. Returns False when nothing was due."""
    async with sessionmaker() as db, db.begin():
        row = await db.scalar(
            select(OutboxMessage)
            .where(
                OutboxMessage.status == PENDING,
                OutboxMessage.next_attempt_at <= datetime.now(UTC),
            )
            .order_by(OutboxMessage.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return False
        message = Message(row.to_email, row.subject, row.text_body or "", row.html_body or "")
        try:
            await mailer.send(message)
        except MailError as error:
            row.attempts += 1
            row.last_error = str(error)[:200]
            if row.attempts >= MAX_ATTEMPTS:
                row.status, row.text_body, row.html_body = DEAD, None, None
                logger.error("mail %s dead after %s attempts: %s", row.id, row.attempts, error)
            else:
                row.next_attempt_at = datetime.now(UTC) + retry_delay(row.attempts)
                logger.warning("mail %s failed (attempt %s): %s", row.id, row.attempts, error)
            return True
        row.status, row.sent_at = SENT, datetime.now(UTC)
        row.text_body, row.html_body, row.last_error = None, None, None
        return True


async def run(stop: asyncio.Event, heartbeat: Path = HEARTBEAT) -> None:
    settings = load_mailer_settings()
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    mailer = mailer_from(settings)
    try:
        while not stop.is_set():
            await asyncio.to_thread(heartbeat.touch)
            try:
                while await process_once(sessionmaker, mailer):
                    await asyncio.to_thread(heartbeat.touch)
            except Exception:
                logger.exception("outbox pass failed; retrying")
            try:
                await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
            except TimeoutError:
                pass
    finally:
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = asyncio.Event()

    async def start() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await run(stop)

    asyncio.run(start())


if __name__ == "__main__":
    main()
