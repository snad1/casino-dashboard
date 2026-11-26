"""Ingest: the healthcheck heartbeat, and one tail and reconcile loop per served service."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.ingest import main as ingest_main
from app.ingest.main import beat
from app.settings import IngestSettings


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


async def test_each_served_service_gets_its_own_tail_and_reconcile_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    started: list[tuple[str, str]] = []

    async def tail(stop: asyncio.Event, settings: Any, service: str) -> None:
        started.append(("tail", service))

    async def reconcile(stop: asyncio.Event, settings: Any, service: str) -> None:
        started.append(("reconcile", service))

    settings = SimpleNamespace(dashboard_services=["wolfbet", "demo"], ingest_tg_data_dir=None)
    monkeypatch.setattr(ingest_main, "load_ingest_settings", lambda: settings)
    monkeypatch.setattr(ingest_main, "tail", tail)
    monkeypatch.setattr(ingest_main, "reconcile", reconcile)
    stop = asyncio.Event()
    stop.set()
    await ingest_main.run(stop, tmp_path / "alive")
    assert sorted(started) == [
        ("reconcile", "demo"),
        ("reconcile", "wolfbet"),
        ("tail", "demo"),
        ("tail", "wolfbet"),
    ]


def test_ingest_refuses_a_service_it_does_not_know() -> None:
    with pytest.raises(ValidationError, match="Unknown service in DASHBOARD_SERVICES: nosuch"):
        IngestSettings(host_tz="UTC", dashboard_services="wolfbet,nosuch")
