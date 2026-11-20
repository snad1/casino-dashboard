"""The mailer process: drains the outbox, one row per transaction, with backoff on failure.

It also delivers alerts: email alerts become outbox rows here, Telegram alerts go straight
to the Bot API, and a second task listens for Telegram link messages.

It runs as its own container, the only one with a route to the internet, so the api never
needs outbound access. Rows are claimed with `FOR UPDATE SKIP LOCKED`, so a second worker
would never send the same message twice.
"""

import asyncio
import logging
import signal
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.channels import TelegramClient, deliver_once, fan_out, poll
from app.db.engine import create_engine, create_sessionmaker
from app.mail.mailers import Mailer, MailError, Message, mailer_from
from app.mail.models import DEAD, PENDING, SENT, OutboxMessage
from app.mail.outbox import MAX_ATTEMPTS, retry_delay
from app.settings import load_mailer_settings

HEARTBEAT = Path("/tmp/mailer.alive")  # noqa: S108  # nosec B108: private tmpfs in the container
POLL_SECONDS = 5.0

logger = logging.getLogger("mailer")


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


async def alerts_once(
    sessionmaker: async_sessionmaker[AsyncSession],
    telegram: TelegramClient | None,
    zone: ZoneInfo,
) -> None:
    """Fan out new alert deliveries, then send every one that is due, one per transaction."""
    async with sessionmaker() as db, db.begin():
        await fan_out(db, datetime.now(UTC))
    while True:
        async with sessionmaker() as db, db.begin():
            if not await deliver_once(db, telegram, zone, datetime.now(UTC)):
                return


async def run(stop: asyncio.Event, heartbeat: Path = HEARTBEAT) -> None:
    settings = load_mailer_settings()
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    mailer = mailer_from(settings)
    zone = ZoneInfo(settings.host_tz)
    token = settings.telegram_bot_token
    telegram = TelegramClient(token.get_secret_value()) if token is not None else None
    if telegram is None:
        logger.info("telegram is off: no notification bot token; telegram alerts wait")
    poller = asyncio.create_task(poll(stop, telegram, sessionmaker)) if telegram else None
    try:
        while not stop.is_set():
            await asyncio.to_thread(heartbeat.touch)
            try:
                await alerts_once(sessionmaker, telegram, zone)
            except Exception:
                logger.exception("alert pass failed; retrying")
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
        if poller is not None:
            await poller
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
