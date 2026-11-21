"""Telegram bot v1 events parse from the shared contract fixtures and always name their user."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.adapters.base import InvalidEvent
from app.adapters.wolfbet import WolfBetAdapter
from app.adapters.wolfbet.events import BetEvent, LifecycleEvent, StateEvent
from app.adapters.wolfbet_tg import WolfBetTgAdapter
from app.adapters.wolfbet_tg.events import TgBetEvent, TgLifecycleEvent, TgStateEvent

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "wolfbet_tg"
ADAPTER = WolfBetTgAdapter()


def _fixture(name: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return document


def _encode(document: Any) -> bytes:
    return json.dumps(document).encode()


def test_fixtures_match_their_published_checksums() -> None:
    lines = (FIXTURES / "SHA256SUMS").read_text().splitlines()
    assert {line.split()[1] for line in lines} == {"bet.json", "state.json", "lifecycle.json"}
    for line in lines:
        digest, name = line.split()
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == digest, name


@pytest.mark.parametrize(
    ("name", "kind", "daemon_kind"),
    [
        ("bet", TgBetEvent, BetEvent),
        ("state", TgStateEvent, StateEvent),
        ("lifecycle", TgLifecycleEvent, LifecycleEvent),
    ],
)
def test_every_fixture_parses_and_reads_as_the_daemons_event(
    name: str, kind: type, daemon_kind: type
) -> None:
    event = ADAPTER.parse((FIXTURES / f"{name}.json").read_bytes())
    assert isinstance(event, kind)
    assert isinstance(event, daemon_kind)
    assert (event.lane, event.pid, event.scope) == (1, 4242, 42)


def _with(name: str, **changes: Any) -> bytes:
    document = _fixture(name)
    for path, value in changes.items():
        target = document
        *parents, leaf = path.split("__")
        for key in parents:
            target = target[key]
        if value is None:
            del target[leaf]
        else:
            target[leaf] = value
    return _encode(document)


@pytest.mark.parametrize(
    "raw",
    [
        _with("bet", svc="wolfbet"),
        _with("bet", payload__uid=None),
        _with("bet", payload__uid=0),
        _with("bet", payload__uid=True),
        _with("bet", payload__uid="42"),
        _with("bet", payload__extra=1),
        _with("bet", lane=2),
        _with("state", payload__uid=None),
        _with("state", payload__uid=False),
        _with("lifecycle", payload__uid=None),
    ],
    ids=[
        "daemon-service",
        "bet-no-uid",
        "uid-zero",
        "bool-uid",
        "string-uid",
        "extra-bet-key",
        "other-lane",
        "state-no-uid",
        "state-bool-uid",
        "lifecycle-no-uid",
    ],
)
def test_events_without_a_valid_user_are_refused(raw: bytes) -> None:
    with pytest.raises(InvalidEvent) as caught:
        ADAPTER.parse(raw)
    assert caught.value.reason == "shape"


def test_daemon_events_keep_no_scope() -> None:
    daemon = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "wolfbet"
    assert WolfBetAdapter().parse((daemon / "bet.json").read_bytes()).scope is None


def test_streams_are_three_per_user_in_a_stable_order() -> None:
    assert ADAPTER.streams == ()
    assert ADAPTER.streams_for([43, 42, 43]) == (
        "wolfbet-tg:42:bets",
        "wolfbet-tg:42:state",
        "wolfbet-tg:42:lifecycle",
        "wolfbet-tg:43:bets",
        "wolfbet-tg:43:state",
        "wolfbet-tg:43:lifecycle",
    )
