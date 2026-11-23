"""The range export: the sessions the report counts, as CSV or JSON, for operators only."""

import csv
import io
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType
from typing import Any

import asyncpg  # type: ignore[import-untyped]  # ships no type hints
import pytest
from fastapi.testclient import TestClient

from app.api.analytics import INVALID_WINDOW
from app.api.history import SessionDetail
from app.auth.models import Role
from app.ingest.tailer import LIVE_DB
from app.settings import Settings
from tests.conftest import make_client
from tests.test_analytics_parity import remove_db
from tests.test_reconcile import copy_all
from tests.wolfbet_db import BotDb, needs_wolfbet

pytestmark = needs_wolfbet

SERVICE = "wolfbet"
URL = f"/{SERVICE}/analytics/export"


async def _clear(pg: asyncpg.Connection) -> None:
    for table in ("bets", "sessions", "reconcile_runs", "db_epochs"):
        await pg.execute(f"DELETE FROM {table} WHERE service = $1", SERVICE)  # noqa: S608  # nosec B608: fixed table names


@pytest.fixture
async def history(
    wolfbet: ModuleType, tmp_path: Path, pg: asyncpg.Connection
) -> AsyncIterator[None]:
    """An archived epoch with one session, then the live one with four across two lanes."""
    await _clear(pg)
    old = BotDb(wolfbet, tmp_path / LIVE_DB)
    old.session(lane=1, started="2026-01-01T10:00:00")
    old.close()
    await copy_all(pg, SERVICE, tmp_path)
    remove_db(tmp_path)

    bot = BotDb(wolfbet, tmp_path / LIVE_DB)
    for lane, started, strategy in (
        (1, "2026-01-05T10:00:00", "Flat"),
        (2, "2026-01-05T11:00:00", '=HYPERLINK("http://x.test")'),
        (1, "2026-01-06T09:00:00", "+cmd"),
        (2, "2026-01-08T09:00:00", "Mart"),
    ):
        session = bot.session(lane=lane, started=started)
        bot.conn.execute("UPDATE sessions SET strategy = ? WHERE id = ?", (strategy, session))
        bot.conn.commit()
    bot.close()
    await copy_all(pg, SERVICE, tmp_path)
    yield
    await _clear(pg)


def _rows(response: Any) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(response.text)))


def test_operators_download_the_windows_sessions_as_csv(signed_in_as: Any, history: None) -> None:
    response = signed_in_as(Role.OPERATOR).get(f"{URL}?since=2026-01-05&until=2026-01-06")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"] == (
        'attachment; filename="wolfbet-sessions-2026-01-05-2026-01-06.csv"'
    )
    rows = _rows(response)
    assert list(rows[0]) == list(SessionDetail.model_fields)
    assert [r["id"] for r in rows] == ["1", "2", "3"]
    assert {r["epoch_id"] for r in rows} == {rows[0]["epoch_id"]}
    assert rows[1]["strategy"] == '\'=HYPERLINK("http://x.test")'
    assert rows[2]["strategy"] == "'+cmd"


def test_json_has_the_same_rows_and_the_lane_filter_applies(
    signed_in_as: Any, history: None
) -> None:
    operator = signed_in_as(Role.OPERATOR)
    response = operator.get(f"{URL}?lane=2&format=json")
    assert response.status_code == 200
    assert response.headers["content-disposition"].endswith('-all-all.json"')
    rows = json.loads(response.text)
    assert [(r["id"], r["lane"]) for r in rows] == [(2, 2), (4, 2)]
    assert rows[0]["strategy"] == '=HYPERLINK("http://x.test")'
    everything = _rows(operator.get(URL))
    assert len(everything) == 4


def test_only_operators_get_a_file_and_bad_input_is_refused(
    control_settings: Settings, signed_in_as: Any
) -> None:
    with make_client(control_settings) as anonymous:
        assert anonymous.get(URL).status_code == 401
    assert signed_in_as(Role.VIEWER, two_factor=False).get(URL).status_code == 403
    operator: TestClient = signed_in_as(Role.OPERATOR)
    assert operator.get("/goated/analytics/export").status_code == 404
    bad = operator.get(f"{URL}?since=2026-13-01")
    assert bad.status_code == 422
    assert bad.json()["detail"] == INVALID_WINDOW
    assert operator.get(f"{URL}?format=xml").status_code == 422
    assert operator.get(f"{URL}?lane=100").status_code == 422
