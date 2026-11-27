from datetime import UTC, datetime, timedelta

from app.history.epochs import EpochView, is_live

HOUR = timedelta(hours=1)
RESET = datetime(2026, 11, 21, 19, 22, 27, tzinfo=UTC)
NEXT = datetime(2026, 11, 21, 19, 40, 5, tzinfo=UTC)


def test_the_first_epoch_starts_at_its_first_session() -> None:
    view = EpochView(ids=[1], first_started_at=RESET)
    assert view.bounds(HOUR) == (RESET, None)


def test_the_bucket_a_reset_falls_in_belongs_to_neither_epoch() -> None:
    old = EpochView(ids=[1], first_started_at=RESET - timedelta(days=30), next_started_at=NEXT)
    new = EpochView(ids=[2], first_started_at=NEXT, has_previous=True)
    assert old.bounds(HOUR)[1] == datetime(2026, 11, 21, 19, tzinfo=UTC)
    assert new.bounds(HOUR)[0] == datetime(2026, 11, 21, 20, tzinfo=UTC)
    assert new.bounds(timedelta(minutes=1))[0] == datetime(2026, 11, 21, 19, 41, tzinfo=UTC)


def test_a_start_on_a_bucket_edge_is_kept() -> None:
    edge = datetime(2026, 11, 21, 20, tzinfo=UTC)
    assert EpochView(ids=[2], first_started_at=edge, has_previous=True).bounds(HOUR)[0] == edge


def test_only_the_live_file_name_is_live() -> None:
    assert is_live("wolfbet", "/botdata/.wolfbet_autobot.db")
    assert not is_live("wolfbet", "/botdata/.wolfbet_autobot-20261121-192227.db")


def test_each_service_is_live_on_its_own_file() -> None:
    assert is_live("goated", "/botdata/.goated_autobot.db")
    assert not is_live("goated", "/botdata/.goated_autobot-20261127-120000.db")
    assert not is_live("goated", "/botdata/.wolfbet_autobot.db")
    assert not is_live("wolfbet", "/botdata/.goated_autobot.db")
    assert is_live("svc-unknown", "/botdata/.wolfbet_autobot.db"), "WolfBet's name by default"
