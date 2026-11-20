"""The dashboard's own health: Prometheus text, status checks and who may read them."""

import json
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.wolfbet import WolfBetAdapter
from app.auth.models import Role
from app.live.aggregator import TickTimer
from app.live.consumer import ServiceConsumer
from app.metrics import DropWatch, Readings, checks, render_prometheus
from app.settings import Settings
from tests.conftest import make_user
from tests.test_live_consumer import _event

FULL = Readings(
    live=True,
    events=120,
    dropped={"schema": 2},
    gaps=1,
    reconnects=3,
    lag_s=0.25,
    last_event_age_s=4.0,
    ws_clients=7,
    ws_closed_slow=1,
    tick_last_s=0.002,
    tick_max_s=0.01,
    ingest_lag_s=30.0,
    ingest_lag_rows=40,
    ingest_lag_alert=False,
    reconcile_mismatched=0,
    agent_up=True,
    agent_latency_s=0.05,
    publisher_dropped={1: 0, 2: 4},
)


def test_prometheus_text_has_every_reading() -> None:
    text = render_prometheus(FULL)
    for line in (
        "# TYPE dashboard_consumer_events_total counter",
        "dashboard_consumer_events_total 120",
        'dashboard_consumer_dropped_total{reason="schema"} 2',
        "dashboard_consumer_gaps_total 1",
        "dashboard_consumer_reconnects_total 3",
        "dashboard_consumer_lag_seconds 0.25",
        "dashboard_last_event_age_seconds 4.0",
        "# TYPE dashboard_ws_clients gauge",
        "dashboard_ws_clients 7",
        "dashboard_ws_closed_slow_total 1",
        'dashboard_tick_seconds{q="last"} 0.002',
        'dashboard_tick_seconds{q="max"} 0.01',
        "dashboard_ingest_lag_seconds 30.0",
        "dashboard_ingest_lag_rows 40",
        "dashboard_reconcile_mismatched 0",
        "dashboard_agent_up 1",
        "dashboard_agent_latency_seconds 0.05",
        'dashboard_publisher_dropped_total{lane="2"} 4',
    ):
        assert line in text.splitlines(), line
    assert text.endswith("\n")


def test_unknown_readings_are_left_out() -> None:
    text = render_prometheus(Readings())
    assert "dashboard_ws_clients" not in text
    assert "dashboard_agent_up" not in text
    assert "dashboard_consumer_events_total 0" in text


def _by_name(readings: Readings, grew: bool = False) -> dict[str, Any]:
    return {c.name: c for c in checks(readings, grew)}


def test_a_healthy_stack_is_all_ok() -> None:
    rows = _by_name(FULL)
    assert all(row.ok for row in rows.values()), [n for n, r in rows.items() if not r.ok]
    assert set(rows) == {
        "Live stream",
        "Stream lag",
        "Dropped events",
        "Live viewers",
        "Tick duration",
        "Ingest lag",
        "Reconcile",
        "Host agent",
    }


@pytest.mark.parametrize(
    ("change", "row"),
    [
        ({"last_event_age_s": 61.0}, "Live stream"),
        ({"last_event_age_s": None}, "Live stream"),
        ({"lag_s": 5.1}, "Stream lag"),
        ({"tick_max_s": 0.11}, "Tick duration"),
        ({"ingest_lag_alert": True}, "Ingest lag"),
        ({"reconcile_mismatched": 2}, "Reconcile"),
        ({"agent_up": False}, "Host agent"),
        ({"agent_latency_s": 1.5}, "Host agent"),
    ],
)
def test_each_threshold_turns_its_row_to_warning(change: dict[str, Any], row: str) -> None:
    readings = Readings(**{**FULL.__dict__, **change})
    rows = _by_name(readings)
    assert rows[row].ok is False
    assert all(r.ok for name, r in rows.items() if name != row)


def test_new_drops_warn_and_old_ones_do_not() -> None:
    assert _by_name(FULL, grew=True)["Dropped events"].ok is False
    assert _by_name(FULL, grew=False)["Dropped events"].value == 2 + 1 + 4


def test_with_live_updates_off_the_stream_row_says_so() -> None:
    rows = _by_name(Readings(agent_up=True))
    assert rows["Live stream"].ok and "off" in rows["Live stream"].detail
    assert "Stream lag" not in rows


def test_drop_watch_compares_with_a_minute_ago() -> None:
    watch = DropWatch(window_s=60)
    assert watch.grew(0, 5) is False
    assert watch.grew(30, 6) is True
    assert watch.grew(70, 6) is True
    assert watch.grew(130, 6) is False


def test_tick_timer_keeps_the_longest_until_two_windows_pass() -> None:
    now = [0.0]
    assert TickTimer().max is None
    timer = TickTimer(window_s=60, clock=lambda: now[0])
    timer.record(0.05)
    timer.record(0.01)
    assert (timer.last, timer.max) == (0.01, 0.05)
    now[0] = 61
    timer.record(0.002)
    assert timer.max == 0.05
    now[0] = 125
    timer.record(0.003)
    assert timer.max == 0.003


async def test_consumer_notes_when_the_last_event_arrived_and_its_lag() -> None:
    async def sink(_: Any) -> None:
        return None

    consumer = ServiceConsumer(None, WolfBetAdapter(), sink)  # type: ignore[arg-type]
    document = _event("bet", pid=1, seq=0)
    document["ts_ms"] = int(time.time() * 1000) - 1500
    await consumer._handle(json.dumps(document).encode())
    assert consumer.last_event_ms is not None
    assert consumer.lag_ms is not None and 1400 <= consumer.lag_ms < 5000


# ── routes ──
@pytest.fixture
def clients(app_settings: Settings, as_user: Any) -> Iterator[dict[str, TestClient]]:
    yield {
        "admin": as_user(make_user(app_settings, Role.ADMIN, two_factor=True)),
        "viewer": as_user(make_user(app_settings, Role.VIEWER, two_factor=False)),
    }


def test_status_is_for_owners_and_admins(clients: dict[str, TestClient]) -> None:
    response = clients["admin"].get("/status")
    assert response.status_code == 200
    body = response.json()
    assert "at" in body
    names = [c["name"] for c in body["checks"]]
    assert names[0] == "Live stream" and "Host agent" in names
    assert clients["viewer"].get("/status").status_code == 403


def test_metrics_answers_only_inside_the_stack(clients: dict[str, TestClient]) -> None:
    client = clients["viewer"]
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "dashboard_consumer_events_total 0" in response.text
    proxied = client.get("/metrics", headers={"x-forwarded-for": "203.0.113.9"})
    assert proxied.status_code == 404
