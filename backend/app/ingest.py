"""History ingest worker (placeholder until the SQLite tail is built).

It only proves liveness: a heartbeat file the container healthcheck reads.
"""

import logging
import signal
import threading
from pathlib import Path

HEARTBEAT = Path("/tmp/ingest.alive")  # noqa: S108  # nosec B108: private tmpfs in the container
INTERVAL_SECONDS = 30.0

logger = logging.getLogger("ingest")


def beat(path: Path = HEARTBEAT) -> None:
    """Touch the heartbeat file so the healthcheck sees a fresh mtime."""
    path.touch()


def run(stop: threading.Event, path: Path = HEARTBEAT, interval: float = INTERVAL_SECONDS) -> None:
    """Beat until stop is set."""
    logger.info("ingest is not wired yet; heartbeat only")
    while not stop.is_set():
        beat(path)
        stop.wait(interval)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    run(stop)


if __name__ == "__main__":
    main()
