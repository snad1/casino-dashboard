"""The pure rule evaluation: each kind fires once, waits out its cooldown and re-arms."""

from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import ValidationError

from app.alerts.engine import State, evaluate, track_day
from app.alerts.rules import Firing, Rule


def rule(kind: str, params: dict[str, Any], cooldown_s: int = 0, rule_id: int = 1) -> Rule:
    return Rule.build(
        id=rule_id,
        service="wolfbet",
        lane=None,
        kind=kind,
        name="Test",
        params=params,
        cooldown_s=cooldown_s,
    )


def run(
    r: Rule, signals: Sequence[tuple[float, dict[str, Any]]], state: State | None = None
) -> tuple[list[Firing], State]:
    fired: list[Firing] = []
    current: State = dict(state or {})
    for now, signal in signals:
        firing, current = evaluate(r, signal, now, current, lane=1)
        if firing is not None:
            fired.append(firing)
    return fired, current


def lane(**fields: Any) -> dict[str, Any]:
    return {"status": "running", "session_id": 1, **fields}


def test_a_profit_threshold_fires_once_and_re_arms_after_clearing() -> None:
    r = rule("profit", {"at": 0.01})
    fired, _ = run(
        r,
        [(t, lane(profit=p)) for t, p in enumerate([0.0, 0.011, 0.02, 0.015, 0.005, 0.012])],
    )
    assert [f.value for f in fired] == [0.011, 0.012]
    assert fired[0].message == "Test: profit +0.011 on lane 1"


def test_the_cooldown_holds_back_a_second_firing() -> None:
    r = rule("loss", {"at": 0.01}, cooldown_s=600)
    signals = [(0, lane(profit=-0.02)), (10, lane(profit=0)), (20, lane(profit=-0.02))]
    fired, state = run(r, signals)
    assert len(fired) == 1
    assert state["armed"] is True
    again, _ = run(r, [(700, lane(profit=-0.02))], state)
    assert len(again) == 1


def test_milestones_fire_at_each_step_and_one_jump_fires_once() -> None:
    r = rule("profit", {"step": 0.001})
    values = [0.0, 0.0009, 0.0011, 0.0019, 0.0021, 0.0055]
    fired, state = run(r, [(t, lane(profit=p)) for t, p in enumerate(values)])
    assert [round(f.value or 0, 6) for f in fired] == [0.001, 0.002, 0.005]
    assert state["next_step"] == pytest.approx(0.006)


def test_a_new_session_restarts_milestones_without_firing() -> None:
    r = rule("profit", {"step": 0.001})
    _, state = run(r, [(0, lane(profit=0.0)), (1, lane(profit=0.0035))])
    fired, state = run(
        r, [(2, lane(session_id=2, profit=0.0)), (3, lane(session_id=2, profit=0.0012))], state
    )
    assert [round(f.value or 0, 6) for f in fired] == [0.001]


def test_a_first_look_at_a_big_profit_does_not_fire_old_milestones() -> None:
    fired, _ = run(rule("profit", {"step": 0.001}), [(0, lane(profit=0.0042))])
    assert fired == []


def test_run_and_day_periods_read_their_own_numbers() -> None:
    fired, _ = run(
        rule("profit", {"period": "run", "at": 1.0}), [(0, lane(profit=0, run_profit=1.5))]
    )
    assert len(fired) == 1
    day = rule("loss", {"period": "day", "at": 0.5})
    firing, _ = evaluate(day, {"day_profit": -0.6, "day": "2026-11-20"}, 0, {})
    assert firing is not None and firing.lane is None


def test_drawdown_tracks_the_peak_and_resets_per_session() -> None:
    r = rule("drawdown", {"pct": 10})
    fired, state = run(r, [(0, lane(balance=100)), (1, lane(balance=120)), (2, lane(balance=107))])
    assert len(fired) == 1 and fired[0].value == pytest.approx(10.8333, rel=1e-3)
    fired, _ = run(
        r, [(3, lane(session_id=2, balance=100)), (4, lane(session_id=2, balance=95))], state
    )
    assert fired == []


def test_bot_down_must_last_the_full_duration() -> None:
    r = rule("down", {"statuses": ["down"], "for_s": 60})
    fired, _ = run(r, [(0, lane(status="down")), (59, lane(status="down"))])
    assert fired == []
    fired, _ = run(
        r, [(0, lane(status="down")), (30, lane(status="running")), (61, lane(status="down"))]
    )
    assert fired == []
    fired, _ = run(
        r, [(0, lane(status="down")), (60, lane(status="down")), (90, lane(status="down"))]
    )
    assert len(fired) == 1 and fired[0].message == "Test: bot down for 60 s on lane 1"


def test_stop_reasons_match_the_list_the_pattern_or_any_stop() -> None:
    stopped = lane(status="stopped", stop_reason="STOPPED: Max loss hit: -0.00100000")
    listed = rule("stop_reason", {"reasons": ["Max loss hit"]})
    assert (
        run(listed, [(0, stopped)])[0][0].message
        == "Test: stopped (Max loss hit: -0.00100000) on lane 1"
    )
    other = rule("stop_reason", {"reasons": ["Profit target reached"]})
    assert run(other, [(0, stopped)])[0] == []
    pattern = rule("stop_reason", {"pattern": "loss hit"})
    assert len(run(pattern, [(0, stopped)])[0]) == 1
    anything = rule("stop_reason", {})
    fired, _ = run(anything, [(0, stopped), (1, stopped), (2, lane()), (3, stopped)])
    assert len(fired) == 2


def test_slow_betting_counts_only_while_running() -> None:
    r = rule("bps_below", {"bps": 5, "for_s": 30})
    fired, _ = run(r, [(0, lane(bps_ewma=2)), (31, lane(bps_ewma=2))])
    assert len(fired) == 1
    fired, _ = run(
        r, [(0, lane(status="paused", bps_ewma=0)), (31, lane(status="paused", bps_ewma=0))]
    )
    assert fired == []


def test_publisher_drops_fire_on_each_increase_after_the_first_look() -> None:
    r = rule("publisher_dropped", {})
    fired, _ = run(
        r,
        [
            (0, lane(dropped=4)),
            (1, lane(dropped=4)),
            (2, lane(dropped=9)),
            (3, lane(dropped=0)),
            (4, lane(dropped=1)),
        ],
    )
    assert [f.value for f in fired] == [5, 1]


def test_ingest_lag_and_mismatches_fire_once_until_they_clear() -> None:
    lag = rule("ingest_lag", {"seconds": 300})
    fired, _ = run(
        lag,
        [
            (0, {"lag_seconds": 400}),
            (1, {"lag_seconds": 900}),
            (2, {"lag_seconds": 10}),
            (3, {"lag_seconds": 301}),
        ],
    )
    assert len(fired) == 2
    flagged = rule("ingest_lag", {})
    assert len(run(flagged, [(0, {"lag_alert": True, "lag_seconds": 5})])[0]) == 1
    mismatch = rule("reconcile_mismatch", {})
    fired, _ = run(
        mismatch,
        [
            (0, {"mismatched": 2}),
            (1, {"mismatched": 3}),
            (2, {"mismatched": 0}),
            (3, {"mismatched": 1}),
        ],
    )
    assert [f.value for f in fired] == [2, 1]


def test_day_profit_adds_changes_and_restarts_at_midnight() -> None:
    day = track_day({"date": "2026-11-20", "total": 1.0, "last": {}}, {1: (5, 0.2)}, "2026-11-20")
    assert day["total"] == 1.0
    day = track_day(day, {1: (5, 0.5), 2: (9, 0.1)}, "2026-11-20")
    assert day["total"] == pytest.approx(1.3)
    day = track_day(day, {1: (6, 0.05), 2: (9, 0.3)}, "2026-11-20")
    assert day["total"] == pytest.approx(1.55)
    day = track_day(day, {1: (6, 0.1)}, "2026-11-21")
    assert day == {"date": "2026-11-21", "total": 0.0, "last": {"1": [6, 0.1]}}


@pytest.mark.parametrize(
    ("kind", "params"),
    [
        ("profit", {}),
        ("profit", {"at": 1, "step": 1}),
        ("profit", {"at": float("inf")}),
        ("loss", {"at": -1}),
        ("drawdown", {"pct": 150}),
        ("down", {"statuses": []}),
        ("down", {"for_s": 0}),
        ("stop_reason", {"reasons": ["Made up"]}),
        ("stop_reason", {"pattern": "(a+)+$"}),
        ("stop_reason", {"pattern": "("}),
        ("stop_reason", {"pattern": "x" * 201}),
        ("bps_below", {"bps": 0}),
        ("publisher_dropped", {"extra": 1}),
    ],
)
def test_bad_params_are_refused(kind: str, params: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        rule(kind, params)


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(ValueError, match="terminal"):
        rule("terminal_opened", {})
