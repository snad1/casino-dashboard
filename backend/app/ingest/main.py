"""The ingest process: tails the bot's SQLite into Timescale and keeps a healthcheck heartbeat.

The heartbeat runs on its own, so a database outage shows up in the logs and lag, not as a
container restart loop. Any failure closes the connection and starts over after a pause.
Reconciliation runs beside the tailer on its own connection, every 15 minutes.
"""

import asyncio
import logging
import signal
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]  # ships no type hints

from app.alerts.engine import after_reconcile
from app.db.engine import create_engine, create_sessionmaker
from app.ingest.reconcile import Reconciler
from app.ingest.tailer import Tailer
from app.settings import IngestSettings, load_ingest_settings

HEARTBEAT = Path("/tmp/ingest.alive")  # noqa: S108  # nosec B108: private tmpfs in the container
BEAT_SECONDS = 30.0
RETRY_SECONDS = 5.0
RECONCILE_SECONDS = 15 * 60.0
RECONCILE_FIRST_SECONDS = 60.0

logger = logging.getLogger("ingest")


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def beat(stop: asyncio.Event, path: Path = HEARTBEAT, interval: float = BEAT_SECONDS) -> None:
    """Touch the heartbeat file until stop is set, so the healthcheck sees a fresh mtime."""
    while not stop.is_set():
        await asyncio.to_thread(path.touch)
        await _sleep(stop, interval)


async def connect(settings: IngestSettings) -> asyncpg.Connection:
    return await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        database=settings.postgres_db,
    )


async def tail(stop: asyncio.Event, settings: IngestSettings, retry: float = RETRY_SECONDS) -> None:
    """Run the tailer until stop is set, reconnecting after any failure."""
    while not stop.is_set():
        try:
            db = await connect(settings)
            try:
                tailer = Tailer(
                    db, settings.ingest_service, settings.bot_data_dir, settings.host_tz
                )
                await tailer.prepare()
                await tailer.run(stop)
            finally:
                await db.close()
        except Exception:
            logger.exception("ingest pass failed; retrying in %s s", retry)
            await _sleep(stop, retry)


async def reconcile(
    stop: asyncio.Event,
    settings: IngestSettings,
    first: float = RECONCILE_FIRST_SECONDS,
    interval: float = RECONCILE_SECONDS,
) -> None:
    """Run one reconcile per interval until stop is set; a failed run waits for the next.

    Each finished run is also the signal for the ingest alert rules (lag, mismatches).
    """
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    await _sleep(stop, first)
    while not stop.is_set():
        row = None
        try:
            db = await connect(settings)
            try:
                reconciler = Reconciler(
                    db, settings.ingest_service, settings.bot_data_dir, settings.host_tz
                )
                row = await reconciler.run_once()
            finally:
                await db.close()
        except Exception:
            logger.exception("reconcile run failed; next one in %s s", interval)
        if row is not None:
            try:
                await after_reconcile(sessionmaker, settings.ingest_service, row)
            except Exception:
                logger.exception("ingest alerts could not be checked")
        await _sleep(stop, interval)
    await engine.dispose()


async def run(stop: asyncio.Event, heartbeat: Path = HEARTBEAT) -> None:
    settings = load_ingest_settings()
    await asyncio.gather(beat(stop, heartbeat), tail(stop, settings), reconcile(stop, settings))


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
