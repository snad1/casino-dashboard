"""Ingest heartbeat: written for the healthcheck, and stops cleanly."""

import asyncio
from pathlib import Path

from app.ingest.main import beat


async def test_beat_writes_heartbeat_and_stops(tmp_path: Path) -> None:
    path = tmp_path / "alive"
    stop = asyncio.Event()
    task = asyncio.create_task(beat(stop, path, 0.01))
    for _ in range(100):
        if path.exists():
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert path.exists()
