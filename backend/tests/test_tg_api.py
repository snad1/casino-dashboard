"""A Telegram user's own history, exports, live status, settings and controls."""

import csv
import io
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.tg import NO_SESSION, REFUSED
from app.audit import AuditLog
from app.auth.models import Role
from app.history.models import DbEpoch
from app.settings import Settings
from tests.conftest import run_in_session
from tests.fake_agent import FakeAgent, default_reply, fail, ok
from tests.tg_links import T0, TgUsers, new_uid

pytest_plugins = ["tests.fake_agent", "tests.control_rig", "tests.tg_rig"]


def _csrf(client: Any) -> dict[str, str]:
    return {"X-CSRF-Token": client.get("/auth/me").json()["csrf_token"]}


def test_sessions_and_bets_are_the_users_own(tg_users: TgUsers) -> None:
    mine, theirs = new_uid(), new_uid()
    epoch = tg_users.history(mine, sessions=2, bets=3)
    other = tg_users.history(theirs, sessions=4, bets=1)
    client = tg_users.client(mine)
    page = client.get(f"/tg/{mine}/sessions").json()
    assert [(s["epoch_id"], s["id"]) for s in page["items"]] == [(epoch, 2), (epoch, 1)]
    assert client.get(f"/tg/{mine}/sessions/{epoch}/1").json()["profit"] == 0.5
    bets = client.get(f"/tg/{mine}/sessions/{epoch}/1/bets").json()["items"]
    assert [b["id"] for b in bets] == [102, 101, 100]
    assert client.get(f"/tg/{mine}/sessions/{other}/1").status_code == 404
    assert client.get(f"/tg/{mine}/sessions/{other}/1/bets").status_code == 404


def test_csv_exports_hold_only_the_users_rows_and_never_run_as_formulas(
    tg_users: TgUsers,
) -> None:
    mine, theirs = new_uid(), new_uid()
    epoch = tg_users.history(mine, sessions=2, bets=3)
    tg_users.history(theirs, sessions=5)
    client = tg_users.client(mine)
    reply = client.get(f"/tg/{mine}/sessions.csv")
    assert reply.headers["content-type"].startswith("text/csv")
    assert reply.headers["cache-control"] == "no-store"
    rows = list(csv.DictReader(io.StringIO(reply.text)))
    assert [r["id"] for r in rows] == ["1", "2"]
    assert rows[0]["strategy"] == "'=cmd"
    bets = list(
        csv.DictReader(io.StringIO(client.get(f"/tg/{mine}/sessions/{epoch}/2/bets.csv").text))
    )
    assert [b["id"] for b in bets] == ["200", "201", "202"]


def test_series_counts_only_the_users_bets(tg_users: TgUsers) -> None:
    mine, theirs = new_uid(), new_uid()
    tg_users.history(mine, sessions=1, bets=3)
    tg_users.history(theirs, sessions=1, bets=9)
    client = tg_users.client(mine)
    since, until = T0.isoformat(), T0.replace(hour=12).isoformat()
    out = client.get(f"/tg/{mine}/series", params={"from": since, "to": until, "res": "1h"}).json()
    assert out["res"] == "1h"
    (lane,) = out["series"]
    assert sum(value for _, value in lane["points"]) == pytest.approx(0.3)


def test_the_admin_list_names_each_telegram_user_with_totals(
    tg_users: TgUsers, signed_in_as: Any
) -> None:
    uid = new_uid()
    tg_users.history(uid, sessions=2, bets=3)
    rows = signed_in_as(Role.ADMIN).get("/tg/users")
    mine = [row for row in rows.json()["items"] if row["uid"] == uid]
    assert mine == [
        {
            "uid": uid,
            "sessions": 2,
            "bets": 6,
            "profit": 1.5,
            "last_session_at": mine[0]["last_session_at"],
        }
    ]


def test_status_and_config_ask_the_bot_for_the_path_uid(
    tg_users: TgUsers, fake_agent: FakeAgent
) -> None:
    mine = new_uid()

    def reply(request: dict[str, Any]) -> list[dict[str, Any]]:
        if request["cmd"] == "wolfbet_tg.status":
            return [ok(request, {"sessions": [{"slot": 1, "profit": 0.2}]})]
        if request["cmd"] == "wolfbet_tg.config":
            return [ok(request, {"config": {"currency": "btc"}})]
        return default_reply(request)

    fake_agent.reply = reply
    client = tg_users.client(mine)
    assert client.get(f"/tg/{mine}/status").json() == {"sessions": [{"slot": 1, "profit": 0.2}]}
    assert client.get(f"/tg/{mine}/config").json() == {"config": {"currency": "btc"}}
    sent = [(c["cmd"], c["args"]) for c in fake_agent.calls if c["cmd"].startswith("wolfbet_tg.")]
    assert sent == [("wolfbet_tg.status", {"uid": mine}), ("wolfbet_tg.config", {"uid": mine})]
    assert all(c["mac_ok"] for c in fake_agent.calls)


def test_a_malformed_bot_reply_is_a_gateway_error(tg_users: TgUsers, fake_agent: FakeAgent) -> None:
    mine = new_uid()
    fake_agent.reply = lambda request: [ok(request, {"unexpected": True})]
    client = tg_users.client(mine)
    assert client.get(f"/tg/{mine}/status").status_code == 502


def test_control_sends_the_path_uid_is_idempotent_and_audited(
    tg_users: TgUsers, fake_agent: FakeAgent, migrated: Settings
) -> None:
    mine = new_uid()
    fake_agent.reply = lambda request: [ok(request, {"slots": [1]})]
    client = tg_users.client(mine)
    headers = {**_csrf(client), "Idempotency-Key": "pause-key-0123456789"}
    first = client.post(f"/tg/{mine}/control/pause", json={"slot": 1}, headers=headers)
    again = client.post(f"/tg/{mine}/control/pause", json={"slot": 1}, headers=headers)
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json() == {"slots": [1], "session_id": None}
    sent = [(c["cmd"], c["args"]) for c in fake_agent.calls if c["cmd"].startswith("wolfbet_tg.")]
    assert sent == [("wolfbet_tg.pause", {"uid": mine, "slot": 1})]
    other = client.post(f"/tg/{mine}/control/pause", json={"slot": 2}, headers=headers)
    assert other.status_code == 422

    async def audited(db: AsyncSession) -> list[tuple[str, str]]:
        rows = await db.execute(
            select(AuditLog.action, AuditLog.result).where(
                AuditLog.action == "control.tg.pause", AuditLog.args["uid"].as_integer() == mine
            )
        )
        return [(action, result) for action, result in rows]

    assert run_in_session(migrated, audited) == [
        ("control.tg.pause", "requested"),
        ("control.tg.pause", "succeeded"),
    ]


def test_start_reports_the_new_slot(tg_users: TgUsers, fake_agent: FakeAgent) -> None:
    mine = new_uid()
    fake_agent.reply = lambda request: [ok(request, {"slot": 2, "session_id": 77})]
    client = tg_users.client(mine)
    headers = {**_csrf(client), "Idempotency-Key": "start-key-0123456789"}
    reply = client.post(f"/tg/{mine}/control/start", json={"slot": 5}, headers=headers)
    assert reply.json() == {"slots": [2], "session_id": 77}
    (call,) = [c for c in fake_agent.calls if c["cmd"] == "wolfbet_tg.start"]
    assert call["args"] == {"uid": mine}


@pytest.mark.parametrize(
    ("code", "status", "detail"),
    [("not_found", 404, NO_SESSION), ("bad_request", 422, REFUSED), ("busy", 409, None)],
)
def test_bot_refusals_read_plainly(
    tg_users: TgUsers, fake_agent: FakeAgent, code: str, status: int, detail: str | None
) -> None:
    mine = new_uid()
    fake_agent.reply = lambda request: [fail(request, code)]
    client = tg_users.client(mine)
    headers = {**_csrf(client), "Idempotency-Key": f"{code}-key-0123456789"}
    reply = client.post(f"/tg/{mine}/control/stop", json={}, headers=headers)
    assert reply.status_code == status
    if detail is not None:
        assert reply.json() == {"detail": detail}


def test_a_new_epoch_of_another_service_is_never_counted(
    tg_users: TgUsers, migrated: Settings
) -> None:
    uid = new_uid()
    tg_users.history(uid, sessions=1)

    async def stray(db: AsyncSession) -> None:
        db.add(
            DbEpoch(service="wolfbet", source_path="/x", inode=uid, first_started_at=T0, tg_uid=uid)
        )
        await db.commit()

    run_in_session(migrated, stray)
    client = tg_users.client(uid)
    assert len(client.get(f"/tg/{uid}/sessions").json()["items"]) == 1
