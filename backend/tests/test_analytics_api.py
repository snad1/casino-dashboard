"""The analytics api: the bot's report through the agent, history when the agent fails."""

from typing import Any

from fastapi.testclient import TestClient

from app.api.analytics import INVALID_WINDOW
from app.auth.models import Role
from app.settings import Settings
from tests.conftest import make_client
from tests.fake_agent import FakeAgent, fail, ok

GROUP = {"sessions": 2, "bets": 300, "wins": 169, "losses": 131, "profit": 0.5, "wagered": 3.0}
TOP = {
    "id": 7,
    "game": "dice",
    "strategy": "Flat Bet",
    "bets": 100,
    "wins": 50,
    "losses": 50,
    "profit": 0.4,
    "wagered": 1.0,
    "started_at": "2026-11-02T10:00:00",
}
REPORT: dict[str, Any] = {
    "v": 1,
    "window": {"since": "2026-11-01", "until": None, "lane": 2},
    "lifetime": {
        "sessions_total": 2,
        "bets_total": 300,
        "wins_total": 169,
        "losses_total": 131,
        "wagered_total": 3.0,
        "profit_total": 0.5,
        "best_profit": 0.4,
        "worst_profit": 0.1,
        "sessions_in_profit": 2,
        "sessions_in_loss": 0,
        "win_rate_pct": 56.333333333333336,
    },
    "span": {
        "first": "2026-11-02T10:00:00",
        "last": "2026-11-02T12:00:00",
        "span_sec": 7200.0,
        "bet_sec": 3600.0,
    },
    "by_game": [{"game": "dice", **GROUP}],
    "by_strategy": [{"strategy": "Flat Bet", **GROUP}],
    "by_lane": [{"lane": "Lane 2", **GROUP}],
    "top_best": [TOP],
    "top_worst": [TOP],
}
URL = "/wolfbet/analytics?since=2026-11-01&lane=2"


def _replying(doc: dict[str, Any], exit_code: int = 0) -> Any:
    return lambda request: [ok(request, {"exit_code": exit_code, "stderr": "", "json": doc})]


def _viewer(signed_in_as: Any) -> TestClient:
    client: TestClient = signed_in_as(Role.VIEWER, two_factor=False)
    return client


def test_viewers_get_the_bots_report_once_a_minute(
    signed_in_as: Any, fake_agent: FakeAgent
) -> None:
    fake_agent.reply = _replying(REPORT)
    viewer = _viewer(signed_in_as)
    first = viewer.get(URL)
    assert first.status_code == 200
    body = first.json()
    assert body["source"] == "bot"
    assert body["zone"] == "UTC"
    assert body["report"] == REPORT
    assert set(body["charts"]) == {"by_hour", "by_day", "profit_bins", "duration_bins"}
    assert viewer.get(URL).json() == body
    assert fake_agent.cmds() == ["wolfbet.analytics"]
    assert fake_agent.calls[0]["args"] == {"since": "2026-11-01", "lane": 2}


def test_an_unreachable_or_broken_agent_falls_back_to_history(
    signed_in_as: Any, fake_agent: FakeAgent
) -> None:
    viewer = _viewer(signed_in_as)
    broken = {**REPORT, "lifetime": {"sessions_total": "lots"}}
    for reply in (
        lambda request: [fail(request, "timeout")],
        _replying(broken),
        _replying({"v": 1, "error": {"code": "no_database", "message": "No database"}}, 1),
        _replying(REPORT, exit_code=1),
    ):
        fake_agent.reply = reply
        response = viewer.get(f"/wolfbet/analytics?since=2099-01-0{len(fake_agent.calls) + 1}")
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "history"
        assert body["report"]["lifetime"]["sessions_total"] == 0
        assert "lots" not in response.text and "No database" not in response.text


def test_a_window_the_bot_refuses_is_a_plain_422(signed_in_as: Any, fake_agent: FakeAgent) -> None:
    fake_agent.reply = _replying(
        {"v": 1, "error": {"code": "invalid_window", "message": "since is after until: /root"}}, 1
    )
    response = _viewer(signed_in_as).get("/wolfbet/analytics?since=2026-11-05&until=2026-11-01")
    assert response.status_code == 422
    assert response.json()["detail"] == INVALID_WINDOW
    assert "/root" not in response.text


def test_bad_input_is_refused_before_the_agent_is_asked(
    signed_in_as: Any, fake_agent: FakeAgent
) -> None:
    viewer = _viewer(signed_in_as)
    for query in ("since=2026-13-01", "until=2026-11-01T25:00", "since=yesterday"):
        response = viewer.get(f"/wolfbet/analytics?{query}")
        assert response.status_code == 422
        assert response.json()["detail"] == INVALID_WINDOW
    for query in ("lane=0", "lane=100", "since=" + "9" * 40):
        assert viewer.get(f"/wolfbet/analytics?{query}").status_code == 422
    assert fake_agent.calls == []


def test_anonymous_users_and_unknown_services_get_nothing(
    control_settings: Settings, signed_in_as: Any, fake_agent: FakeAgent
) -> None:
    with make_client(control_settings) as anonymous:
        assert anonymous.get(URL).status_code == 401
    assert _viewer(signed_in_as).get("/goated/analytics").status_code == 404
    assert fake_agent.calls == []
