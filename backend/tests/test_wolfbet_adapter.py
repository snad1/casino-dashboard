"""WolfBet v1 events parse from the shared contract fixtures; anything else is refused."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.adapters.base import InvalidEvent
from app.adapters.wolfbet import WolfBetAdapter
from app.adapters.wolfbet.events import BetEvent, LifecycleEvent, StateEvent

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "wolfbet"
ADAPTER = WolfBetAdapter()


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
    ("name", "kind"), [("bet", BetEvent), ("state", StateEvent), ("lifecycle", LifecycleEvent)]
)
def test_every_fixture_parses_to_its_type(name: str, kind: type) -> None:
    event = ADAPTER.parse((FIXTURES / f"{name}.json").read_bytes())
    assert isinstance(event, kind)
    assert (event.lane, event.pid) == (2, 4242)


def test_bet_fields_are_typed() -> None:
    event = ADAPTER.parse(_encode(_fixture("bet")))
    assert isinstance(event, BetEvent)
    assert event.payload.state == "win"
    assert event.payload.balance_after == pytest.approx(2.52702731)


def test_state_payload_stays_open_for_new_bot_fields() -> None:
    document = _fixture("state")
    document["payload"]["field_added_later"] = 1
    event = ADAPTER.parse(_encode(document))
    assert isinstance(event, StateEvent)
    assert event.payload["field_added_later"] == 1


def _bet(**changes: Any) -> dict[str, Any]:
    document = _fixture("bet")
    for path, value in changes.items():
        target = document
        *parents, leaf = path.split("__")
        for key in parents:
            target = target[key]
        target[leaf] = value
    return document


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"{not json", "json"),
        (b"[1, 2]", "shape"),
        (_encode(_bet(v=2)), "version"),
        (_encode({k: v for k, v in _bet().items() if k != "v"}), "version"),
        (_encode(_bet(svc="goated")), "shape"),
        (_encode(_bet(type="mystery")), "shape"),
        (_encode(_bet(extra_top_level=1)), "shape"),
        (_encode(_bet(payload__unknown_field=1)), "shape"),
        (_encode(_bet(payload__state="draw")), "shape"),
        (_encode(_bet(payload__total_bets=-1)), "shape"),
        (_encode(_bet(payload__total_bets=True)), "shape"),
        (_encode(_bet(lane=0)), "shape"),
        (_encode(_bet(seq="17")), "shape"),
    ],
    ids=[
        "bad-json",
        "not-an-object",
        "version-2",
        "no-version",
        "other-service",
        "unknown-type",
        "extra-envelope-key",
        "extra-bet-key",
        "bad-bet-state",
        "negative-count",
        "bool-as-int",
        "lane-zero",
        "seq-as-string",
    ],
)
def test_malformed_events_are_refused_with_a_reason(raw: bytes, reason: str) -> None:
    with pytest.raises(InvalidEvent) as caught:
        ADAPTER.parse(raw)
    assert caught.value.reason == reason


def test_lifecycle_kind_must_be_known() -> None:
    document = _fixture("lifecycle")
    document["payload"]["kind"] = "explode"
    with pytest.raises(InvalidEvent):
        ADAPTER.parse(_encode(document))
