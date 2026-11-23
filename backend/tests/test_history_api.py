"""History routes: viewer-only, stable keyset pages, point-budgeted series, current totals."""

import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import history
from app.auth.models import Role, User
from app.history.epochs import EPOCH_NOT_FOUND
from app.ingest.tailer import ARCHIVE_GLOB, LIVE_DB
from app.settings import Settings
from tests.conftest import NO_LOGIN, make_user, new_email, run_in_session

T0 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)


class Seed:
    """Writes history rows for one throwaway service, the way ingest stores them."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.service = f"svc-{uuid.uuid4().hex[:8]}"

    def _run(self, sql: str, params: dict[str, Any], returning: bool = False) -> Any:
        async def step(session: AsyncSession) -> Any:
            result = await session.execute(text(sql), params)
            value = result.scalar() if returning else None
            await session.commit()
            return value

        return run_in_session(self.settings, step)

    def epoch(
        self,
        inode: int = 1,
        archived: bool = False,
        started: datetime = T0,
        tg_uid: int | None = None,
    ) -> int:
        """A database epoch; an archived one carries the name `wolfctl reset` gives it."""
        name = ARCHIVE_GLOB.replace("*", f"2026010{inode}-000000") if archived else LIVE_DB
        epoch_id: int = self._run(
            "INSERT INTO db_epochs (service, source_path, inode, first_started_at, tg_uid)"
            " VALUES (:s, :path, :inode, :t, :uid) RETURNING epoch_id",
            {
                "s": self.service,
                "path": f"/botdata/{name}",
                "inode": inode,
                "t": started,
                "uid": tg_uid,
            },
            returning=True,
        )
        return epoch_id

    def session(
        self, epoch_id: int, src_id: int, lane: int = 1, ended: bool = True, profit: float = 1.0
    ) -> None:
        self._run(
            "INSERT INTO sessions (service, epoch_id, src_id, lane, started_at, ended_at,"
            " currency, game, total_bets, profit)"
            " VALUES (:s, :e, :id, :lane, :start, :end, 'btc', 'dice', 10, :p)",
            {
                "s": self.service,
                "e": epoch_id,
                "id": src_id,
                "lane": lane,
                "start": T0 + timedelta(minutes=src_id),
                "end": T0 + timedelta(minutes=src_id, seconds=30) if ended else None,
                "p": profit,
            },
        )

    def bet(
        self,
        epoch_id: int,
        src_id: int,
        ts: datetime,
        session: int = 1,
        lane: int = 1,
        profit: float = 0.5,
        balance: float = 100.0,
    ) -> None:
        self._run(
            "INSERT INTO bets (service, epoch_id, src_id, ts, session_src_id, lane, amount,"
            " state, profit, balance_after)"
            " VALUES (:s, :e, :id, :ts, :session, :lane, 1.0, 'win', :p, :b)",
            {
                "s": self.service,
                "e": epoch_id,
                "id": src_id,
                "ts": ts,
                "session": session,
                "lane": lane,
                "p": profit,
                "b": balance,
            },
        )

    def reconcile_run(self, epoch_id: int, mismatched: int, lag_alert: bool) -> None:
        self._run(
            "INSERT INTO reconcile_runs (service, epoch_id, kind, lo_id, hi_id, started_at,"
            " finished_at, sessions_checked, mismatched, purged_upstream, persisting, lag_rows,"
            " lag_seconds, lag_alert, details) VALUES (:s, :e, 'quick', 0, 10, :t, :t, 1, :m, 0,"
            " :m, 5, 42.0, :a, '{}')",
            {"s": self.service, "e": epoch_id, "t": T0, "m": mismatched, "a": lag_alert},
        )

    def clean(self) -> None:
        for table in ("bets", "sessions", "reconcile_runs", "db_epochs"):
            self._run(f"DELETE FROM {table} WHERE service = :s", {"s": self.service})  # noqa: S608  # nosec B608: fixed table names


@pytest.fixture
def seed(app_settings: Settings) -> Iterator[Seed]:
    data = Seed(app_settings)
    yield data
    data.clean()


@pytest.fixture
def viewer(app_settings: Settings, as_user: Any) -> TestClient:
    client: TestClient = as_user(make_user(app_settings, Role.VIEWER, two_factor=False))
    return client


def test_anonymous_and_roleless_users_are_refused(
    app_settings: Settings, client: TestClient, as_user: Any, seed: Seed
) -> None:
    assert client.get(f"/{seed.service}/sessions").status_code == 401

    async def roleless(session: AsyncSession) -> User:
        user = User(email=new_email(), password_hash=NO_LOGIN)
        session.add(user)
        await session.commit()
        return user

    nobody = as_user(run_in_session(app_settings, roleless))
    for path in ("sessions", "series", "current", "ingest", "sessions/1/1", "sessions/1/1/bets"):
        assert nobody.get(f"/{seed.service}/{path}").status_code == 403


def test_session_pages_stay_stable_while_sessions_arrive(viewer: TestClient, seed: Seed) -> None:
    epoch = seed.epoch()
    for src_id in range(1, 6):
        seed.session(epoch, src_id)
    first = viewer.get(f"/{seed.service}/sessions", params={"limit": 2}).json()
    assert [s["id"] for s in first["items"]] == [5, 4]
    seed.session(epoch, 6)
    seed.session(epoch, 7)
    second = viewer.get(
        f"/{seed.service}/sessions", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert [s["id"] for s in second["items"]] == [3, 2]
    third = viewer.get(
        f"/{seed.service}/sessions", params={"limit": 2, "cursor": second["next_cursor"]}
    ).json()
    assert [s["id"] for s in third["items"]] == [1]
    assert third["next_cursor"] is None


def test_sessions_filter_by_lane_and_status(viewer: TestClient, seed: Seed) -> None:
    epoch = seed.epoch()
    seed.session(epoch, 1, lane=1)
    seed.session(epoch, 2, lane=2)
    seed.session(epoch, 3, lane=1, ended=False)
    lane_one = viewer.get(f"/{seed.service}/sessions", params={"lane": 1}).json()["items"]
    assert [s["id"] for s in lane_one] == [3, 1]
    running = viewer.get(f"/{seed.service}/sessions", params={"status": "running"}).json()
    assert [s["id"] for s in running["items"]] == [3]
    bad = viewer.get(f"/{seed.service}/sessions", params={"cursor": "not-a-cursor"})
    assert bad.status_code == 422


def test_sessions_filter_takes_lane_ids_up_to_99(viewer: TestClient, seed: Seed) -> None:
    epoch = seed.epoch()
    seed.session(epoch, 1, lane=15)
    seed.session(epoch, 2, lane=1)
    lane_15 = viewer.get(f"/{seed.service}/sessions", params={"lane": 15}).json()["items"]
    assert [s["id"] for s in lane_15] == [1]
    assert viewer.get(f"/{seed.service}/sessions", params={"lane": 100}).status_code == 422


def test_first_page_counts_every_matching_session_and_later_pages_do_not(
    viewer: TestClient, seed: Seed
) -> None:
    epoch = seed.epoch()
    for src_id in range(1, 6):
        seed.session(epoch, src_id, lane=1 if src_id % 2 else 2)
    first = viewer.get(f"/{seed.service}/sessions", params={"limit": 2}).json()
    assert first["total"] == 5
    assert first["items"][0]["game"] == "dice"
    lane_one = viewer.get(f"/{seed.service}/sessions", params={"lane": 1, "limit": 1}).json()
    assert lane_one["total"] == 3
    later = viewer.get(
        f"/{seed.service}/sessions", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert later["total"] is None


def test_session_detail_and_unknown_session(viewer: TestClient, seed: Seed) -> None:
    epoch = seed.epoch()
    seed.session(epoch, 1, profit=2.5)
    detail = viewer.get(f"/{seed.service}/sessions/{epoch}/1")
    assert detail.status_code == 200
    body = detail.json()
    assert (body["epoch_id"], body["id"], body["profit"], body["currency"]) == (
        epoch,
        1,
        2.5,
        "btc",
    )
    assert "peak_bps" in body
    missing = viewer.get(f"/{seed.service}/sessions/{epoch}/99")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Session not found."
    assert viewer.get(f"/{seed.service}/sessions/{epoch}/99/bets").status_code == 404


def test_session_bets_page_newest_first(viewer: TestClient, seed: Seed) -> None:
    epoch = seed.epoch()
    seed.session(epoch, 1)
    for src_id in range(1, 8):
        seed.bet(epoch, src_id, T0 + timedelta(seconds=src_id))
    path = f"/{seed.service}/sessions/{epoch}/1/bets"
    pages: list[list[int]] = []
    before = None
    while True:
        params: dict[str, Any] = {"limit": 3}
        if before is not None:
            params["before"] = before
        page = viewer.get(path, params=params).json()
        pages.append([b["id"] for b in page["items"]])
        before = page["next_before"]
        if before is None:
            break
    assert pages == [[7, 6, 5], [4, 3, 2], [1]]


def test_series_picks_the_finest_resolution_within_budget(viewer: TestClient, seed: Seed) -> None:
    epoch = seed.epoch()
    seed.bet(epoch, 1, T0 + timedelta(seconds=1), profit=0.5, balance=100.5)
    seed.bet(epoch, 2, T0 + timedelta(seconds=1, milliseconds=500), profit=-1.0, balance=99.5)
    seed.bet(epoch, 3, T0 + timedelta(minutes=3), lane=2, profit=2.0, balance=50.0)
    path = f"/{seed.service}/series"
    window = {"from": T0.isoformat(), "to": (T0 + timedelta(hours=2)).isoformat()}

    auto = viewer.get(path, params=window).json()
    assert auto["res"] == "1m"
    assert [(s["lane"], [p[1] for p in s["points"]]) for s in auto["series"]] == [
        (1, [-0.5]),
        (2, [2.0]),
    ]

    too_fine = viewer.get(path, params={**window, "res": "1s"})
    assert too_fine.status_code == 422
    assert "coarser" in too_fine.json()["detail"]

    short = {"from": T0.isoformat(), "to": (T0 + timedelta(minutes=10)).isoformat()}
    raw = viewer.get(path, params={**short, "metric": "balance", "lane": 1}).json()
    assert raw["res"] == "1s"
    assert raw["series"] == [{"lane": 1, "points": [[_iso(T0 + timedelta(seconds=1)), 99.5]]}]

    backwards = viewer.get(path, params={"from": short["to"], "to": short["from"]})
    assert backwards.status_code == 422


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def test_current_is_each_lanes_latest_session_in_the_newest_epoch(
    viewer: TestClient, seed: Seed
) -> None:
    old = seed.epoch(inode=1, archived=True)
    seed.session(old, 9, lane=3, ended=False)
    epoch = seed.epoch(inode=2)
    seed.session(epoch, 1, lane=1)
    seed.session(epoch, 2, lane=2, ended=False)
    seed.session(epoch, 3, lane=1, ended=False)
    lanes = viewer.get(f"/{seed.service}/current").json()["lanes"]
    assert [(s["lane"], s["id"], s["epoch_id"]) for s in lanes] == [(1, 3, epoch), (2, 2, epoch)]
    assert all(s["ended_at"] is None for s in lanes)


def test_identical_queries_share_a_result_for_one_second(
    viewer: TestClient, seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(history, "CACHE_SECONDS", 0.3)
    epoch = seed.epoch()
    seed.session(epoch, 1)
    path = f"/{seed.service}/sessions"
    assert len(viewer.get(path).json()["items"]) == 1
    seed.session(epoch, 2)
    assert len(viewer.get(path).json()["items"]) == 1
    time.sleep(0.35)
    assert len(viewer.get(path).json()["items"]) == 2


def test_ingest_status_is_the_latest_reconcile_run(viewer: TestClient, seed: Seed) -> None:
    path = f"/{seed.service}/ingest"
    assert viewer.get(path).json() == {"last_run": None}
    epoch = seed.epoch()
    seed.reconcile_run(epoch, mismatched=0, lag_alert=False)
    seed.reconcile_run(epoch, mismatched=2, lag_alert=True)
    time.sleep(1.05)
    run = viewer.get(path).json()["last_run"]
    assert (run["mismatched"], run["persisting"], run["lag_alert"]) == (2, 2, True)
    assert (run["lag_rows"], run["lag_seconds"], run["kind"]) == (5, 42.0, "quick")


def test_after_a_reset_every_view_is_empty_until_the_next_session(
    viewer: TestClient, seed: Seed
) -> None:
    old = seed.epoch(inode=1, archived=True)
    seed.session(old, 1, lane=1, ended=False)
    seed.bet(old, 1, T0 + timedelta(minutes=1))
    base = f"/{seed.service}"
    window = {"from": T0.isoformat(), "to": (T0 + timedelta(hours=1)).isoformat()}
    assert viewer.get(f"{base}/sessions").json()["items"] == []
    assert viewer.get(f"{base}/current").json()["lanes"] == []
    assert viewer.get(f"{base}/series", params=window).json()["series"] == []

    picked = viewer.get(f"{base}/sessions", params={"epoch": old}).json()["items"]
    assert [(s["epoch_id"], s["id"]) for s in picked] == [(old, 1)]
    points = viewer.get(f"{base}/series", params=window | {"epoch": old}).json()["series"]
    assert [p["lane"] for p in points] == [1]


def test_the_current_epoch_hides_the_archived_ones(viewer: TestClient, seed: Seed) -> None:
    old = seed.epoch(inode=1, archived=True)
    seed.session(old, 1)
    new = seed.epoch(inode=2, started=T0 + timedelta(days=1))
    seed.session(new, 1)
    items = viewer.get(f"/{seed.service}/sessions").json()["items"]
    assert [s["epoch_id"] for s in items] == [new]


def test_epochs_list_the_current_database_and_each_reset(viewer: TestClient, seed: Seed) -> None:
    old = seed.epoch(inode=1, archived=True)
    seed.session(old, 1)
    new = seed.epoch(inode=2, started=T0 + timedelta(days=1))
    seed.epoch(inode=3, tg_uid=42)
    items = viewer.get(f"/{seed.service}/epochs").json()["items"]
    assert [(e["epoch_id"], e["current"]) for e in items] == [(new, True), (old, False)]
    assert items[0]["last_started_at"] is None
    assert items[1]["last_started_at"] is not None


def test_an_unknown_or_telegram_epoch_is_not_found(viewer: TestClient, seed: Seed) -> None:
    seed.epoch(inode=1)
    user_db = seed.epoch(inode=2, tg_uid=42)
    for epoch in (user_db, 999_999):
        reply = viewer.get(f"/{seed.service}/sessions", params={"epoch": epoch})
        assert reply.status_code == 404
        assert reply.json()["detail"] == EPOCH_NOT_FOUND
