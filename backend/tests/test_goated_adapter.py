"""goated v1 events parse from the shared contract fixtures; its control menu offers only the
verbs goatedctl has, with bodies pinned to the agent's contract; its analytics report reads as
WolfBet's."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from app import registry
from app.adapters.base import InvalidEvent
from app.adapters.goated import GoatedAdapter
from app.adapters.goated.control import ACTIONS, NOT_OFFERED
from app.adapters.goated.events import GoatedBetEvent, GoatedLifecycleEvent, GoatedStateEvent
from app.adapters.wolfbet.analytics import AnalyticsReport
from app.adapters.wolfbet.control import ACTIONS as WOLFBET_ACTIONS
from app.adapters.wolfbet.control import ConfigArgs, NoArgs

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
FIXTURES = CONTRACTS / "fixtures" / "goated"
ADAPTER = GoatedAdapter()


def _fixture(name: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return document


def _encode(document: Any) -> bytes:
    return json.dumps(document).encode()


def test_fixtures_match_their_published_checksums() -> None:
    for line in (FIXTURES / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split()
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == digest, name


@pytest.mark.parametrize(
    ("name", "kind"),
    [("bet", GoatedBetEvent), ("state", GoatedStateEvent), ("lifecycle", GoatedLifecycleEvent)],
)
def test_every_fixture_parses_to_its_type(name: str, kind: type) -> None:
    event = ADAPTER.parse((FIXTURES / f"{name}.json").read_bytes())
    assert isinstance(event, kind)
    assert (event.lane, event.pid, event.scope) == (2, 4242, None)


def test_a_bet_keeps_the_game_target_and_the_casinos_result_text() -> None:
    event = ADAPTER.parse(_encode(_fixture("bet")))
    assert isinstance(event, GoatedBetEvent)
    bet = event.payload
    assert (bet.game, bet.multiplier_target, bet.multiplier) == ("limbo", 2.0, 2.0)
    assert (bet.result_value, bet.result_display) == (2.31, "2.31x")


def _bet(**changes: Any) -> dict[str, Any]:
    document = _fixture("bet")
    for path, value in changes.items():
        target = document
        *parents, leaf = path.split("__")
        for key in parents:
            target = target[key]
        target[leaf] = value
    return document


def _without(key: str) -> dict[str, Any]:
    document = _bet()
    del document["payload"][key]
    return document


WOLFBET_BET = json.loads((CONTRACTS / "fixtures" / "wolfbet" / "bet.json").read_text())


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"{not json", "json"),
        (b"[1, 2]", "shape"),
        (_encode(_bet(v=2)), "version"),
        (_encode(_bet(svc="wolfbet")), "shape"),
        (_encode(WOLFBET_BET), "shape"),
        (_encode(_bet(extra_top_level=1)), "shape"),
        (_encode(_bet(payload__rule="over")), "shape"),
        (_encode(_bet(payload__game="crash")), "shape"),
        (_encode(_bet(payload__result_display=2.31)), "shape"),
        (_encode(_without("result_display")), "shape"),
        (_encode(_without("multiplier_target")), "shape"),
        (_encode(_bet(payload__total_bets=-1)), "shape"),
    ],
)
def test_anything_off_contract_is_refused_with_a_reason(raw: bytes, reason: str) -> None:
    with pytest.raises(InvalidEvent) as error:
        ADAPTER.parse(raw)
    assert error.value.reason == reason


def test_the_streams_and_the_registry_entry() -> None:
    assert ADAPTER.streams == ("goated:bets", "goated:state", "goated:lifecycle")
    entry = registry.KNOWN["goated"]
    assert (entry.name, entry.label, entry.ctl, entry.adapter) == (
        "goated",
        "Goated",
        "goatedctl",
        GoatedAdapter,
    )
    assert (entry.files.live_db, entry.files.archive_glob, entry.files.config) == (
        ".goated_autobot.db",
        ".goated_autobot-*.db",
        ".goated_autobot.json",
    )
    assert entry.controls is ACTIONS
    assert not registry.is_enabled("goated"), "goated is served only once a deployment says so"


def test_the_menu_is_wolfbets_without_what_goated_cannot_do() -> None:
    assert NOT_OFFERED == {"pause", "resume", "reload"}
    assert set(ACTIONS) == set(WOLFBET_ACTIONS) - NOT_OFFERED
    assert "0-amount bet" in ACTIONS["benchmark"].effect
    assert "goatedctl" in ACTIONS["version"].effect
    assert "{branch}" not in ACTIONS["update"].effect
    for name, action in ACTIONS.items():
        theirs = WOLFBET_ACTIONS[name]
        assert (action.perm, action.cmd, action.job, action.fresh, action.exclusive) == (
            theirs.perm,
            theirs.cmd,
            theirs.job,
            theirs.fresh,
            theirs.exclusive,
        ), name


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("benchmark", {"count": 5, "lanes": 2}),
        ("backtest", {"sessions": 10}),
        ("backtest", {"simulate": 10, "replay_session": 3}),
        ("update", {"branch": "main"}),
    ],
)
def test_a_flag_goated_does_not_take_is_refused_before_the_agent(
    name: str, body: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        ACTIONS[name].body.model_validate(body)


def test_every_body_matches_the_agent_contract() -> None:
    contract = json.loads((CONTRACTS / "schemas" / "agent-goated-args-v1.json").read_text())
    assert set(contract) == {action.cmd for action in ACTIONS.values()}
    for name, spec in ACTIONS.items():
        body: type[BaseModel] = spec.body
        if spec.agent_args is not None:
            assert ConfigArgs.model_json_schema() == contract[spec.cmd], name
            continue
        if spec.fixed_args:
            assert set(spec.fixed_args) <= set(contract[spec.cmd]["properties"]), name
            continue
        if body is NoArgs:
            agent = contract[spec.cmd]["properties"]
            assert all("default" in field for field in agent.values()), name
            continue
        assert body.model_json_schema() == contract[spec.cmd], name


def _shape(name: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((CONTRACTS / "schemas" / name).read_text())
    for key in ("$id", "title", "description"):
        document.pop(key, None)
    return document


def test_goateds_analytics_report_reads_as_wolfbets() -> None:
    assert _shape("goated-cli-analytics-v1.json") == _shape("wolfbet-cli-analytics-v1.json")
    report = {
        "window": {"since": None, "until": None, "lane": None},
        "lifetime": {
            key: 1.0
            for key in (
                "sessions_total",
                "bets_total",
                "wins_total",
                "losses_total",
                "wagered_total",
                "profit_total",
                "best_profit",
                "worst_profit",
                "sessions_in_profit",
                "sessions_in_loss",
                "win_rate_pct",
            )
        },
        "span": {"first": None, "last": None, "span_sec": 0.0, "bet_sec": 0.0},
        "by_game": [],
        "by_strategy": [],
        "by_lane": [],
        "top_best": [],
        "top_worst": [],
    }
    assert AnalyticsReport.model_validate({"v": 1, **report}).lifetime.bets_total == 1.0
