"""Control actions reach the agent only for the right role, step-up and key, once per key."""

import json
import secrets
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, SecretStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.wolfbet.control import ACTIONS
from app.api.control import AGENT_DOWN, BUSY, JOB_NOT_FOUND, KEY_REUSED
from app.audit import AuditLog
from app.auth.models import ApiSession, Role, UserRole
from app.auth.stepup import STEP_UP_NEEDED
from app.settings import Settings
from app.ws.routes import NOT_PERMITTED
from tests.conftest import Account, make_client, run_in_session
from tests.fake_agent import JOB_ID, TOKEN, FakeAgent, fail, job_summary, ok
from tests.test_totp import _enrol
from tests.test_ws_auth import _connect, _login, _ticket

ARGS_CONTRACT = Path(__file__).resolve().parents[2] / "contracts/schemas/agent-wolfbet-args-v1.json"
BODIES: dict[str, dict[str, Any]] = {
    "pause": {"target": "all"},
    "resume": {"target": 2},
    "reload": {},
    "config_set": {"pairs": {"base_bet": "0.0001"}},
    "lanes": {"count": 3, "split": "budget"},
    "cleanup": {"days": 7},
    "update": {"branch": "main"},
}
JOBS = {name for name, spec in ACTIONS.items() if spec.job}
ALLOWED: dict[Role, set[str]] = {
    Role.VIEWER: set(),
    Role.OPERATOR: {
        "start",
        "stop",
        "pause",
        "resume",
        "reload",
        "restart",
        "config_set",
        "lanes",
    },
    Role.ADMIN: set(ACTIONS),
    Role.OWNER: set(ACTIONS),
}


def _key() -> str:
    return secrets.token_urlsafe(24)


def _post(
    client: TestClient, action: str, body: dict[str, Any] | None = None, key: str | None = None
) -> Any:
    token = client.get("/auth/me").json()["csrf_token"]
    headers = {"x-csrf-token": token, "idempotency-key": key or _key()}
    payload = BODIES.get(action, {}) if body is None else body
    return client.post(f"/wolfbet/control/{action}", json=payload, headers=headers)


def _set_role(settings: Settings, user_id: int, role: Role) -> None:
    async def run(session: AsyncSession) -> None:
        await session.execute(update(UserRole).where(UserRole.user_id == user_id).values(role=role))
        await session.commit()

    run_in_session(settings, run)


def _expire_step_up(settings: Settings, user_id: int) -> None:
    async def run(session: AsyncSession) -> None:
        query = update(ApiSession).where(ApiSession.user_id == user_id)
        await session.execute(query.values(step_up_at=None))
        await session.commit()

    run_in_session(settings, run)


def _audit_results(settings: Settings, email: str, action: str) -> list[str]:
    async def run(session: AsyncSession) -> list[str]:
        query = (
            select(AuditLog.result)
            .where(AuditLog.actor_email == email, AuditLog.action == f"control.{action}")
            .order_by(AuditLog.id)
        )
        return list(await session.scalars(query))

    return run_in_session(settings, run)


@pytest.fixture
def control_settings(app_settings: Settings, fake_agent: FakeAgent) -> Settings:
    return app_settings.model_copy(
        update={"agent_socket": fake_agent.path, "agent_token": SecretStr(TOKEN)}
    )


@pytest.fixture
def signed_in_as(control_settings: Settings, account: Account) -> Iterator[Any]:
    """The account signed in with `role`; with 2FA turned on and just confirmed if asked."""
    clients: list[TestClient] = []

    def make(role: Role, *, two_factor: bool = True) -> TestClient:
        _set_role(control_settings, account.id, role)
        client = make_client(control_settings).__enter__()
        clients.append(client)
        if two_factor:
            _enrol(client, account.email)
        else:
            _login(client, account.email)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.mark.parametrize("role", list(ALLOWED))
def test_every_action_for_every_role(signed_in_as: Any, fake_agent: FakeAgent, role: Role) -> None:
    client = signed_in_as(role, two_factor=role != Role.VIEWER)
    for action in ACTIONS:
        response = _post(client, action)
        if action not in ALLOWED[role]:
            assert response.status_code == 403, action
            continue
        assert response.status_code == (202 if action in JOBS else 200), action
        body = response.json()
        if action in JOBS:
            assert body == {"status": "running", "exit_code": None, "job_id": JOB_ID}
        else:
            assert body == {"status": "succeeded", "exit_code": 0, "job_id": None}
    sent = {call["cmd"] for call in fake_agent.calls}
    assert all(call["mac_ok"] for call in fake_agent.calls)
    if role == Role.VIEWER:
        assert sent == set()


def test_each_action_sends_its_command_and_validated_args(
    signed_in_as: Any, fake_agent: FakeAgent
) -> None:
    client = signed_in_as(Role.ADMIN)
    for action in ("pause", "config_set", "update", "doctor_fix"):
        assert _post(client, action).status_code < 300
    pause, config, update_job, doctor = fake_agent.calls
    assert (pause["cmd"], pause["args"]) == ("wolfbet.pause", {"target": "all"})
    assert (config["cmd"], config["args"]) == (
        "wolfbet.config_set",
        {"pairs": {"base_bet": "0.0001"}},
    )
    assert update_job["cmd"] == "job.start"
    assert update_job["args"] == {"cmd": "wolfbet.update", "args": {"branch": "main"}}
    assert doctor["args"] == {"cmd": "wolfbet.doctor", "args": {"fix": True}}


def test_a_failed_command_reports_its_exit_code(signed_in_as: Any, fake_agent: FakeAgent) -> None:
    fake_agent.reply = lambda request: [ok(request, {"exit_code": 3, "stderr": "no", "stdout": ""})]
    response = _post(signed_in_as(Role.OPERATOR), "stop")
    assert response.status_code == 200
    assert response.json() == {"status": "failed", "exit_code": 3, "job_id": None}


def test_config_and_destructive_actions_need_a_fresh_step_up(
    signed_in_as: Any, fake_agent: FakeAgent, control_settings: Settings, account: Account
) -> None:
    client = signed_in_as(Role.ADMIN)
    _expire_step_up(control_settings, account.id)
    for action in ("config_set", "lanes", "reset", "update", "doctor_fix"):
        response = _post(client, action)
        assert response.status_code == 403, action
        assert response.json() == {"detail": STEP_UP_NEEDED, "step_up_required": True}
    assert _post(client, "pause").status_code == 200
    assert fake_agent.cmds() == ["wolfbet.pause"]


def test_a_retry_with_the_same_key_gets_the_same_answer_and_runs_once(
    signed_in_as: Any, fake_agent: FakeAgent
) -> None:
    client = signed_in_as(Role.ADMIN)
    key = _key()
    first = _post(client, "update", key=key)
    again = _post(client, "update", key=key)
    assert (first.status_code, again.status_code) == (202, 202)
    assert again.json() == first.json()
    assert fake_agent.cmds() == ["job.start"]
    reused = _post(client, "update", {"branch": "dev"}, key=key)
    assert reused.status_code == 422
    assert reused.json() == {"detail": KEY_REUSED}
    other_action = _post(client, "restart", key=key)
    assert other_action.status_code == 422
    assert fake_agent.cmds() == ["job.start"]


def test_a_missing_or_malformed_key_is_refused(signed_in_as: Any, fake_agent: FakeAgent) -> None:
    client = signed_in_as(Role.OPERATOR)
    token = client.get("/auth/me").json()["csrf_token"]
    missing = client.post("/wolfbet/control/start", json={}, headers={"x-csrf-token": token})
    assert missing.status_code == 422
    assert _post(client, "start", key="short").status_code == 422
    assert _post(client, "start", key="x" * 20 + "!").status_code == 422
    assert fake_agent.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"target": "all", "extra": 1},
        {"target": 0},
        {"target": "2"},
        {"target": "some"},
    ],
)
def test_bad_bodies_never_reach_the_agent(
    signed_in_as: Any, fake_agent: FakeAgent, body: dict[str, Any]
) -> None:
    assert _post(signed_in_as(Role.OPERATOR), "pause", body).status_code == 422
    assert fake_agent.calls == []


def test_bad_branch_and_lane_count_are_refused(signed_in_as: Any, fake_agent: FakeAgent) -> None:
    client = signed_in_as(Role.ADMIN)
    assert _post(client, "update", {"branch": "--upload-pack=x"}).status_code == 422
    assert _post(client, "update", {"branch": "a/../b"}).status_code == 422
    assert _post(client, "lanes", {"count": 11}).status_code == 422
    assert fake_agent.calls == []


def test_a_busy_agent_is_a_conflict(signed_in_as: Any, fake_agent: FakeAgent) -> None:
    fake_agent.reply = lambda request: [fail(request, "busy")]
    client = signed_in_as(Role.OPERATOR)
    key = _key()
    response = _post(client, "restart", key=key)
    assert response.status_code == 409
    assert response.json() == {"detail": BUSY}
    assert _post(client, "restart", key=key).json() == {"detail": BUSY}
    assert fake_agent.cmds() == ["job.start"]


@pytest.mark.parametrize("code", ["unreachable", "timeout", "internal", "bad_request"])
def test_agent_errors_are_one_generic_502(
    signed_in_as: Any, fake_agent: FakeAgent, code: str
) -> None:
    fake_agent.reply = lambda request: [fail(request, code)]
    response = _post(signed_in_as(Role.OPERATOR), "pause")
    assert response.status_code == 502
    assert response.json() == {"detail": AGENT_DOWN}
    assert code not in response.text
    assert TOKEN not in response.text


def test_an_unreachable_agent_is_a_502(app_settings: Settings, account: Account) -> None:
    missing = app_settings.model_copy(
        update={"agent_socket": Path("/nonexistent/agent.sock"), "agent_token": SecretStr(TOKEN)}
    )
    _set_role(missing, account.id, Role.OPERATOR)
    with make_client(missing) as client:
        _login(client, account.email)
        response = _post(client, "start")
    assert response.status_code == 502
    assert response.json() == {"detail": AGENT_DOWN}


def test_without_an_agent_configured_control_is_a_502(
    app_settings: Settings, account: Account
) -> None:
    _set_role(app_settings, account.id, Role.OPERATOR)
    with make_client(app_settings) as client:
        _login(client, account.email)
        response = _post(client, "start")
    assert response.status_code == 502


def test_each_action_is_audited_before_and_after(
    signed_in_as: Any, control_settings: Settings, account: Account, fake_agent: FakeAgent
) -> None:
    client = signed_in_as(Role.ADMIN)
    _post(client, "pause")
    _post(client, "update")
    fake_agent.reply = lambda request: [fail(request, "timeout")]
    _post(client, "stop")
    assert _audit_results(control_settings, account.email, "pause") == ["requested", "succeeded"]
    assert _audit_results(control_settings, account.email, "update") == [
        "requested",
        f"job:{JOB_ID}",
    ]
    assert _audit_results(control_settings, account.email, "stop") == ["requested", "agent:timeout"]


def test_other_services_have_no_controls(signed_in_as: Any, fake_agent: FakeAgent) -> None:
    client = signed_in_as(Role.ADMIN)
    token = client.get("/auth/me").json()["csrf_token"]
    headers = {"x-csrf-token": token, "idempotency-key": _key()}
    response = client.post("/goated/control/pause", json={"target": "all"}, headers=headers)
    assert response.status_code == 404
    assert fake_agent.calls == []


def test_a_job_is_shown_to_who_may_run_it_and_hidden_from_others(
    signed_in_as: Any, fake_agent: FakeAgent, control_settings: Settings, account: Account
) -> None:
    admin = signed_in_as(Role.ADMIN)
    job_id = _post(admin, "update").json()["job_id"]
    shown = admin.get(f"/wolfbet/jobs/{job_id}")
    assert shown.status_code == 200
    assert shown.json() == {
        "job_id": JOB_ID,
        "cmd": "wolfbet.update",
        "status": "running",
        "exit_code": None,
        "duration_ms": 5,
        "lines": 2,
    }
    assert admin.get(f"/wolfbet/jobs/{'cd' * 16}").json() == {"detail": JOB_NOT_FOUND}
    assert admin.get(f"/goated/jobs/{job_id}").status_code in (403, 404)
    _set_role(control_settings, account.id, Role.OPERATOR)
    assert admin.get(f"/wolfbet/jobs/{job_id}").status_code == 404
    fake_agent.reply = lambda request: [fail(request, "not_found")]
    _set_role(control_settings, account.id, Role.ADMIN)
    assert admin.get(f"/wolfbet/jobs/{job_id}").status_code == 404


def test_body_models_match_the_agent_argument_contract() -> None:
    """The api refuses exactly what the agent would: both pin their schemas in contracts/."""
    contract = json.loads(ARGS_CONTRACT.read_text())
    for name, spec in ACTIONS.items():
        body: type[BaseModel] = spec.body
        if spec.fixed_args:
            assert set(spec.fixed_args) <= set(contract[spec.cmd]["properties"]), name
            continue
        assert body.model_json_schema() == contract[spec.cmd], name


def test_job_output_streams_on_the_socket_to_who_may_run_it(
    control_settings: Settings, account: Account, fake_agent: FakeAgent
) -> None:
    live = control_settings.model_copy(update={"live_service": "wolfbet"})
    _set_role(live, account.id, Role.ADMIN)
    with make_client(live) as client:
        _enrol(client, account.email)
        topic = f"svc:wolfbet:job:{_post(client, 'update').json()['job_id']}"
        with _connect(client, _ticket(client)) as socket:
            socket.send_json({"op": "subscribe", "topic": topic})
            messages = [socket.receive_json() for _ in range(3)]
            _set_role(live, account.id, Role.OPERATOR)
            socket.send_json({"op": "subscribe", "topic": topic})
            refused = socket.receive_json()
    assert messages == [
        {"op": "job", "topic": topic, "seq": 0, "stream": "stdout", "line": "step 0"},
        {"op": "job", "topic": topic, "seq": 1, "stream": "stdout", "line": "step 1"},
        {"op": "job_end", "topic": topic, "status": "succeeded", "exit_code": 0},
    ]
    assert refused == {"op": "error", "topic": topic, "detail": NOT_PERMITTED}
    stream = next(call for call in fake_agent.calls if call["cmd"] == "job.stream")
    assert stream["args"] == {"job_id": JOB_ID, "from_seq": 0}


def test_an_unknown_job_topic_is_refused(control_settings: Settings, account: Account) -> None:
    live = control_settings.model_copy(update={"live_service": "wolfbet"})
    _set_role(live, account.id, Role.ADMIN)
    with make_client(live) as client:
        _enrol(client, account.email)
        topic = f"svc:wolfbet:job:{'ef' * 16}"
        with _connect(client, _ticket(client)) as socket:
            socket.send_json({"op": "subscribe", "topic": topic})
            assert socket.receive_json() == {"op": "error", "topic": topic, "detail": NOT_PERMITTED}


def test_the_menu_lists_every_action_with_its_confirm_text(
    signed_in_as: Any, fake_agent: FakeAgent
) -> None:
    menu = signed_in_as(Role.VIEWER, two_factor=False).get("/wolfbet/control")
    assert menu.status_code == 200
    body = menu.json()
    assert [a["name"] for a in body["actions"]] == list(ACTIONS)
    by_name = {a["name"]: a for a in body["actions"]}
    assert by_name["reset"] == {
        "name": "reset",
        "label": "Reset statistics",
        "title": "Reset statistics for {service}?",
        "effect": ACTIONS["reset"].effect,
        "confirm": "Reset statistics",
        "perm": "control.destructive",
        "typed_confirm": True,
        "job": False,
    }
    typed = {name for name, action in by_name.items() if action["typed_confirm"]}
    assert typed == {"reset", "run_reset", "stop_all", "cleanup", "update", "doctor_fix"}
    assert by_name["restart"]["job"] and not by_name["pause"]["typed_confirm"]
    assert body["running_job"] is None
    assert fake_agent.calls == []


def test_the_menu_shows_a_running_job_to_who_may_see_it(
    signed_in_as: Any, fake_agent: FakeAgent, control_settings: Settings, account: Account
) -> None:
    client = signed_in_as(Role.ADMIN)
    _post(client, "update")
    assert client.get("/wolfbet/control").json()["running_job"]["job_id"] == JOB_ID
    fake_agent.reply = lambda request: [
        ok(request, job_summary("succeeded", 0))
        if request["cmd"] == "job.get"
        else fail(request, "busy")
    ]
    assert client.get("/wolfbet/control").json()["running_job"] is None
    fake_agent.reply = lambda request: [fail(request, "unreachable")]
    down = client.get("/wolfbet/control")
    assert (down.status_code, down.json()["running_job"]) == (200, None)
    fake_agent.reply = lambda request: [ok(request, job_summary())]
    _set_role(control_settings, account.id, Role.OPERATOR)
    assert client.get("/wolfbet/control").json()["running_job"] is None


def test_a_service_without_controls_has_no_menu(signed_in_as: Any) -> None:
    assert signed_in_as(Role.ADMIN).get("/goated/control").status_code == 404
