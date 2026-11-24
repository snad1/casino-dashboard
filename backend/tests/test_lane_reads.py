"""A user with a role on one lane reads that lane and nothing from the others or the service."""

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.channels import _recipients
from app.alerts.models import AlertEvent, AlertRule, UserChannels
from app.api.control import ControlRequest
from app.auth.models import Role, User, UserRole
from app.auth.permissions import FORBIDDEN, LaneScope, Perm, lane_scope
from app.auth.sessions import current_user
from app.comments import TARGET_NOT_FOUND, Comment
from app.db.models import Service
from app.settings import Settings
from tests.conftest import NO_LOGIN, make_client, make_user, new_email, run_in_session
from tests.fake_agent import FakeAgent, ok
from tests.test_analytics_api import REPORT
from tests.test_history_api import T0, Seed
from tests.test_ws_auth import _allowed

WOLFBET = "wolfbet"
OWN_JOB = "a" * 32
OTHER_JOB = "b" * 32
_created: list[int] = []


@pytest.fixture(autouse=True)
def _drop_lane_users(app_settings: Settings) -> Iterator[None]:
    """Remove the lane users a test made, so no lane role outlives it (the migration
    round trip drops the lane column and refuses leftovers)."""
    yield

    async def drop(session: AsyncSession) -> None:
        await session.execute(delete(User).where(User.id.in_(_created)))
        await session.commit()

    run_in_session(app_settings, drop)
    _created.clear()


def _lane_user(settings: Settings, service: str, *lanes: int, role: Role = Role.VIEWER) -> User:
    """A user whose only roles are `role` on each of `lanes` of `service`."""

    async def run(session: AsyncSession) -> User:
        await session.execute(
            text(
                "INSERT INTO services (name, adapter, host) VALUES (:n, 'wolfbet', :h)"
                " ON CONFLICT (name) DO NOTHING"
            ),
            {"n": service, "h": f"{service}.test"},
        )
        service_id = await session.scalar(
            text("SELECT id FROM services WHERE name = :n"), {"n": service}
        )
        user = User(email=new_email(), password_hash=NO_LOGIN)
        session.add(user)
        await session.flush()
        _created.append(user.id)
        session.add_all(
            UserRole(user_id=user.id, service_id=service_id, role=role, lane=lane) for lane in lanes
        )
        await session.commit()
        return user

    return run_in_session(settings, run)


@pytest.fixture
def seed(app_settings: Settings) -> Iterator[Seed]:
    data = Seed(app_settings)
    yield data

    async def drop(session: AsyncSession) -> None:
        await session.execute(delete(Comment).where(Comment.service == data.service))
        await session.execute(delete(Service).where(Service.name == data.service))
        await session.commit()

    run_in_session(app_settings, drop)
    data.clean()


@pytest.fixture
def lanes_seeded(seed: Seed) -> int:
    """The current epoch with sessions 1 on lane 1 and 2, 3 on lane 3, one bet each."""
    epoch = seed.epoch()
    for src_id, lane in ((1, 1), (2, 3), (3, 3)):
        seed.session(epoch, src_id, lane=lane, ended=src_id != 3)
        seed.bet(epoch, src_id, T0 + timedelta(minutes=src_id), session=src_id, lane=lane)
    return epoch


@pytest.fixture
def client_as(control_settings: Settings) -> Iterator[Any]:
    """A client on the fake agent whose requests run as a given user."""
    clients: list[TestClient] = []

    def make(user: User) -> TestClient:
        client = make_client(control_settings)
        client.app.dependency_overrides[current_user] = lambda: user  # type: ignore[attr-defined]
        clients.append(client.__enter__())
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def lane3(app_settings: Settings, as_user: Any, seed: Seed) -> TestClient:
    client: TestClient = as_user(_lane_user(app_settings, seed.service, 3))
    return client


def _refused(response: Any) -> bool:
    return bool(response.status_code == 403 and response.json()["detail"] == FORBIDDEN)


def test_sessions_list_and_count_only_their_lanes(
    lane3: TestClient, seed: Seed, lanes_seeded: int
) -> None:
    page = lane3.get(f"/{seed.service}/sessions").json()
    assert [(s["id"], s["lane"]) for s in page["items"]] == [(3, 3), (2, 3)]
    assert page["total"] == 2
    assert lane3.get(f"/{seed.service}/sessions?lane=3").json()["total"] == 2
    assert _refused(lane3.get(f"/{seed.service}/sessions?lane=1"))


def test_another_lanes_session_and_bets_read_as_not_found(
    lane3: TestClient, seed: Seed, lanes_seeded: int
) -> None:
    epoch = lanes_seeded
    assert lane3.get(f"/{seed.service}/sessions/{epoch}/2").json()["lane"] == 3
    assert [
        b["id"] for b in lane3.get(f"/{seed.service}/sessions/{epoch}/2/bets").json()["items"]
    ] == [2]
    for path in (f"sessions/{epoch}/1", f"sessions/{epoch}/1/bets"):
        response = lane3.get(f"/{seed.service}/{path}")
        assert (response.status_code, response.json()["detail"]) == (404, "Session not found.")


def test_series_has_only_their_lanes(lane3: TestClient, seed: Seed, lanes_seeded: int) -> None:
    window = f"from={T0.isoformat()}&to={(T0 + timedelta(hours=1)).isoformat()}&res=1s"
    window = window.replace("+", "%2B")
    series = lane3.get(f"/{seed.service}/series?{window}").json()["series"]
    assert [s["lane"] for s in series] == [3]
    assert _refused(lane3.get(f"/{seed.service}/series?{window}&lane=1"))


def test_current_and_epochs_count_only_their_lanes(
    lane3: TestClient, seed: Seed, lanes_seeded: int
) -> None:
    current = lane3.get(f"/{seed.service}/current").json()["lanes"]
    assert [(s["lane"], s["id"]) for s in current] == [(3, 3)]
    seed.session(lanes_seeded, 4, lane=1)
    [epoch] = lane3.get(f"/{seed.service}/epochs").json()["items"]
    assert epoch["last_started_at"].startswith((T0 + timedelta(minutes=3)).isoformat()[:16])


def test_service_health_is_refused(lane3: TestClient, seed: Seed, lanes_seeded: int) -> None:
    assert _refused(lane3.get(f"/{seed.service}/ingest"))


def test_a_cached_answer_never_crosses_scopes(
    app_settings: Settings, as_user: Any, seed: Seed, lanes_seeded: int
) -> None:
    viewer = make_user(app_settings, Role.VIEWER, two_factor=False)
    lane_only = _lane_user(app_settings, seed.service, 3)
    client = as_user(viewer)
    path = f"/{seed.service}/sessions"
    for user, total in ((viewer, 3), (lane_only, 2), (viewer, 3)):
        client.app.dependency_overrides[current_user] = lambda user=user: user
        assert client.get(path).json()["total"] == total


def test_a_user_without_any_lane_role_here_is_refused(
    app_settings: Settings, as_user: Any, seed: Seed, lanes_seeded: int
) -> None:
    elsewhere = as_user(_lane_user(app_settings, f"{seed.service}-x", 3))
    try:
        assert _refused(elsewhere.get(f"/{seed.service}/sessions"))
    finally:

        async def drop(session: AsyncSession) -> None:
            await session.execute(delete(Service).where(Service.name == f"{seed.service}-x"))
            await session.commit()

        run_in_session(app_settings, drop)


def test_comments_open_only_on_their_lanes_sessions_and_bets(
    app_settings: Settings, lane3: TestClient, seed: Seed, lanes_seeded: int
) -> None:
    epoch = lanes_seeded
    author = make_user(app_settings, Role.VIEWER, two_factor=False)

    async def add(session: AsyncSession) -> None:
        for src_id in (1, 2):
            session.add(
                Comment(
                    service=seed.service,
                    kind="session",
                    author_id=author.id,
                    body=f"note {src_id}",
                    epoch_id=epoch,
                    session_src_id=src_id,
                )
            )
        await session.commit()

    run_in_session(app_settings, add)
    base = f"/{seed.service}/comments?kind="
    own = lane3.get(f"{base}session&epoch_id={epoch}&session_id=2")
    assert [c["body"] for c in own.json()["items"]] == ["note 2"]
    assert lane3.get(f"{base}bet&epoch_id={epoch}&bet_id=2").json()["items"] == []
    for target in (f"session&epoch_id={epoch}&session_id=1", f"bet&epoch_id={epoch}&bet_id=1"):
        other = lane3.get(base + target)
        assert (other.status_code, other.json()["detail"]) == (404, TARGET_NOT_FOUND)
    window = f"from={T0.isoformat()}&to={(T0 + timedelta(hours=1)).isoformat()}".replace("+", "%2B")
    assert _refused(lane3.get(f"{base}chart&{window}"))


def test_live_topics_open_only_their_lanes_and_sessions(
    app_settings: Settings, seed: Seed, lanes_seeded: int
) -> None:
    user = _lane_user(app_settings, seed.service, 3)
    name = seed.service
    cases = [
        (f"svc:{name}:lane:3", True),
        (f"svc:{name}:session:2", True),
        (f"svc:{name}:lane:1", False),
        (f"svc:{name}:session:1", False),
        (f"svc:{name}:session:99", False),
        (f"svc:{name}", False),
        (f"svc:{name}:system", False),
        (f"svc:{name}:comments", False),
    ]
    for topic, expected in cases:
        assert _allowed(app_settings, user.id, topic) is expected, topic


def test_analytics_and_its_export_cover_one_of_their_lanes(
    control_settings: Settings, client_as: Any, fake_agent: FakeAgent, seed: Seed
) -> None:
    seed.service = WOLFBET
    seed.clean()
    epoch = seed.epoch()
    seed.session(epoch, 1, lane=1)
    seed.session(epoch, 2, lane=3)
    fake_agent.reply = lambda request: [ok(request, {"exit_code": 0, "stderr": "", "json": REPORT})]
    one = client_as(_lane_user(control_settings, WOLFBET, 3, role=Role.OPERATOR))
    assert one.get(f"/{WOLFBET}/analytics").status_code == 200
    assert fake_agent.calls[0]["args"] == {"lane": 3}
    assert _refused(one.get(f"/{WOLFBET}/analytics?lane=1"))
    rows = one.get(f"/{WOLFBET}/analytics/export?format=json").json()
    assert [(r["id"], r["lane"]) for r in rows] == [(2, 3)]
    assert _refused(one.get(f"/{WOLFBET}/analytics/export?lane=1"))

    two = client_as(_lane_user(control_settings, WOLFBET, 3, 4))
    assert _refused(two.get(f"/{WOLFBET}/analytics"))
    assert len(fake_agent.calls) == 1


@pytest.fixture
def wolfbet_alerts(app_settings: Settings) -> Iterator[None]:
    """Rules and events on lane 1, lane 3 and the whole service."""

    async def add(session: AsyncSession) -> None:
        for lane in (1, 3, None):
            session.add(AlertRule(service=WOLFBET, lane=lane, kind="down", name=f"rule {lane}"))
            session.add(
                AlertEvent(service=WOLFBET, lane=lane, kind="down", message=f"event {lane}")
            )
        await session.commit()

    async def drop(session: AsyncSession) -> None:
        await session.execute(delete(AlertEvent).where(AlertEvent.service == WOLFBET))
        await session.execute(delete(AlertRule).where(AlertRule.service == WOLFBET))
        await session.commit()

    run_in_session(app_settings, drop)
    run_in_session(app_settings, add)
    yield
    run_in_session(app_settings, drop)


def test_alert_rules_and_events_show_only_their_lanes(
    app_settings: Settings, as_user: Any, wolfbet_alerts: None
) -> None:
    lane3 = as_user(_lane_user(app_settings, WOLFBET, 3))
    assert [r["name"] for r in lane3.get(f"/{WOLFBET}/alerts/rules").json()] == ["rule 3"]
    events = lane3.get(f"/{WOLFBET}/alerts/events").json()["items"]
    assert [e["message"] for e in events] == ["event 3"]


def test_an_alert_reaches_a_lane_user_only_from_their_lane(app_settings: Settings) -> None:
    user = _lane_user(app_settings, WOLFBET, 3)

    async def recipients(session: AsyncSession) -> dict[int | None, bool]:
        session.add(UserChannels(user_id=user.id, email_enabled=True))
        await session.commit()
        found = {}
        for lane in (3, 1, None):
            pairs = await _recipients(session, WOLFBET, "email", lane)
            found[lane] = user.id in {uid for uid, _ in pairs}
        return found

    assert run_in_session(app_settings, recipients) == {3: True, 1: False, None: False}


def test_a_lane_user_follows_their_own_jobs_only(
    control_settings: Settings, client_as: Any, fake_agent: FakeAgent
) -> None:
    user = _lane_user(control_settings, WOLFBET, 3, role=Role.OPERATOR)
    other = make_user(control_settings, Role.OPERATOR, two_factor=True)

    async def add(session: AsyncSession) -> None:
        for owner, job in ((user, OWN_JOB), (other, OTHER_JOB)):
            session.add(
                ControlRequest(
                    user_id=owner.id,
                    service=WOLFBET,
                    idem_key=job,
                    body_sha256=b"x",
                    perm="control.destructive",
                    state="done",
                    job_id=job,
                )
            )
        await session.commit()

    run_in_session(control_settings, add)
    fake_agent.reply = lambda request: [
        ok(
            request,
            {
                "job_id": OWN_JOB,
                "cmd": "lane_restart",
                "status": "succeeded",
                "exit_code": 0,
                "duration_ms": 5,
                "lines": 1,
            },
        )
    ]
    client = client_as(user)
    assert client.get(f"/{WOLFBET}/jobs/{OWN_JOB}").json()["status"] == "succeeded"
    assert client.get(f"/{WOLFBET}/jobs/{OTHER_JOB}").status_code == 404
    assert fake_agent.cmds() == ["job.get"]


def test_the_scope_is_every_lane_for_a_service_role_and_the_granted_lanes_otherwise(
    app_settings: Settings, seed: Seed
) -> None:
    viewer = make_user(app_settings, Role.VIEWER, two_factor=False)
    lanes_only = _lane_user(app_settings, seed.service, 3, 5)

    async def scopes(session: AsyncSession) -> tuple[Any, Any, Any]:
        return (
            await lane_scope(session, viewer, seed.service, Perm.VIEW),
            await lane_scope(session, lanes_only, seed.service, Perm.VIEW),
            await lane_scope(session, lanes_only, seed.service, Perm.CONFIG_WRITE),
        )

    assert run_in_session(app_settings, scopes) == (None, frozenset({3, 5}), frozenset())
    scope = LaneScope(lanes_only, frozenset({3, 5}))
    assert (scope.allows(3), scope.allows(1), scope.allows(None)) == (True, False, False)
    assert scope.cache_tag() == "&lanes=3,5"
    assert LaneScope(lanes_only, frozenset({3})).one(None) == 3
    assert LaneScope(viewer, None).one(None) is None
