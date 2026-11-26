"""Every served service runs its own live pipeline: its own hub, stream and agent reads, so one
service's refused stream never stops another's ticks."""

import json
import random
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis import Redis

from app import registry
from app.adapters.wolfbet import WolfBetAdapter
from app.adapters.wolfbet.control import ACTIONS
from app.auth.models import Role
from app.settings import Settings
from tests.conftest import Account, RedisServer, make_client
from tests.control_rig import set_role
from tests.fake_agent import FakeAgent, ok
from tests.test_aggregator import bet
from tests.test_ws_auth import _connect, _login, _ticket

WOLFBET, DEMO = "wolfbet", "demo"


class DemoAdapter(WolfBetAdapter):
    """A second service whose streams the test Redis refuses to the dashboard user."""

    def __init__(self) -> None:
        super().__init__()
        self.name = DEMO
        self.streams = ("demo:bets", "demo:state", "demo:lifecycle")


@pytest.fixture
def demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        registry.KNOWN,
        DEMO,
        registry.ServiceEntry(DEMO, "Demo", DemoAdapter, ACTIONS, registry.KNOWN[WOLFBET].files),
    )
    monkeypatch.setattr(registry, "_enabled", registry.enabled())


@pytest.fixture
def live(control_settings: Settings, account: Account, demo: None) -> Iterator[TestClient]:
    set_role(control_settings, account.id, Role.OWNER)
    served = control_settings.model_copy(
        update={"dashboard_services": [WOLFBET, DEMO], "live_service": WOLFBET}
    )
    with make_client(served) as client:
        _login(client, account.email)
        yield client


def _publish_bets(redis_server: RedisServer, count: int) -> None:
    publisher = Redis.from_url(redis_server.url("publisher", redis_server.publisher_password))
    pid = random.randint(10_000, 2_000_000)  # noqa: S311  # an id, not a secret
    for seq in range(count):
        event = bet(lane=3, seq=seq).model_dump(mode="json")
        event["pid"] = pid
        publisher.xadd("wolfbet:bets", {"data": json.dumps(event)})
    publisher.close()


def _until(check: Any, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.05)
    return False


def test_each_service_gets_its_own_hub_and_alert_engine(live: TestClient) -> None:
    state = live.app.state  # type: ignore[attr-defined]
    assert set(state.hubs) == {WOLFBET, DEMO}
    assert set(state.alerts) == {WOLFBET, DEMO}
    assert state.hub.svc == WOLFBET
    with _connect(live, _ticket(live)) as socket:
        for svc in (WOLFBET, DEMO):
            socket.send_json({"op": "subscribe", "topic": f"svc:{svc}"})
            assert socket.receive_json()["op"] == "snapshot"


def test_a_refused_stream_never_stops_another_services_ticks(
    live: TestClient, redis_server: RedisServer
) -> None:
    with _connect(live, _ticket(live)) as socket:
        socket.send_json({"op": "subscribe", "topic": "svc:wolfbet"})
        assert socket.receive_json()["op"] == "snapshot"
        _publish_bets(redis_server, 5)
        tick = socket.receive_json()
        assert tick["op"] == "tick" and "3" in tick["lanes"]
    pipelines = {p.svc: p for p in live.app.state.pipelines}  # type: ignore[attr-defined]
    assert _until(lambda: pipelines[DEMO].consumer.stats["reconnects"] > 0)
    assert pipelines[WOLFBET].consumer.stats["reconnects"] == 0


def _one_lane(request: dict[str, Any]) -> list[dict[str, Any]]:
    """The agent of a bot with lane 1 only: lane ids, then an empty state for everything else."""
    if request["cmd"].endswith((".lane_ids", ".lane_ids_enabled")):
        return [ok(request, {"exit_code": 0, "stdout": "1\n"})]
    return [ok(request, {"exit_code": 0, "json": {}})]


@pytest.fixture
def one_lane(fake_agent: FakeAgent) -> FakeAgent:
    fake_agent.reply = _one_lane
    return fake_agent


def test_resync_reads_each_service_from_its_own_commands(
    one_lane: FakeAgent, live: TestClient
) -> None:
    fake_agent = one_lane
    for svc in (WOLFBET, DEMO):
        assert _until(lambda svc=svc: f"{svc}.status" in fake_agent.cmds()), fake_agent.cmds()
        assert f"{svc}.config_show" in fake_agent.cmds()
