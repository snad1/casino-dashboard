"""Ingest placeholder: heartbeat for the healthcheck and a clean stop."""

import threading
from pathlib import Path

from app.ingest import run


def test_run_writes_heartbeat_and_stops(tmp_path: Path) -> None:
    beat = tmp_path / "alive"
    stop = threading.Event()
    worker = threading.Thread(target=run, args=(stop, beat, 0.01))
    worker.start()
    for _ in range(100):
        if beat.exists():
            break
        threading.Event().wait(0.01)
    stop.set()
    worker.join(timeout=2)
    assert beat.exists()
    assert not worker.is_alive()
