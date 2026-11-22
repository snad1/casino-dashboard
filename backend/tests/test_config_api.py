"""The config editor's api: schema and values for writers only, dry runs built on the server."""

import json
from pathlib import Path
from typing import Any

from app.api.config import CONFIG_UNREADABLE, INVALID_VALUE
from app.api.control import AGENT_DOWN
from app.auth.models import Role
from tests.fake_agent import FakeAgent, fail, ok

SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[2] / "agent/tests/fixtures/wolfbet-config-schema.json"
    ).read_text()
)
SHOW = {
    "v": 1,
    "api_key": {"set": True, "masked": "abcd...wxyz"},
    "config": {"max_loss": 0.02, "multiplier": 2.01, "proxy": None, "api_key": "leak"},
    "live_base_bet": 0.0001,
    "run_bumps": None,
    "lanes": 3,
    "lane_split": "budget",
    "lane_overrides": {"2": {"multiplier": 3.0, "api_key": "leak"}},
    "config_file": {"path": "/root/.wolfbet_autobot.json", "mode": "0600", "size": 900},
}
DIFF = {
    "v": 1,
    "base": [{"key": "max_loss", "old": 0.02, "new": 0.01, "apply": "live"}],
    "lanes": {
        "2": [{"key": "multiplier", "old": 3.0, "new": 4.0, "apply": "restart", "removed": False}]
    },
    "warnings": [],
}
CHANGES = {
    "changes": [
        {"key": "max_loss", "value": "0.01", "lane": None},
        {"key": "multiplier", "value": "4", "lane": 2},
    ]
}


def _healthy(request: dict[str, Any]) -> list[dict[str, Any]]:
    doc = {
        "wolfbet.config_schema": SCHEMA,
        "wolfbet.config_show": SHOW,
        "wolfbet.config_dry_run": DIFF,
    }[request["cmd"]]
    return [ok(request, {"exit_code": 0, "stderr": "", "json": doc})]


def _preview(client: Any, body: dict[str, Any]) -> Any:
    token = client.get("/auth/me").json()["csrf_token"]
    return client.post("/wolfbet/config/preview", json=body, headers={"x-csrf-token": token})


def test_writers_get_the_schema_and_saved_values_without_secrets(
    signed_in_as: Any, fake_agent: FakeAgent
) -> None:
    fake_agent.reply = _healthy
    response = signed_in_as(Role.OPERATOR).get("/wolfbet/config")
    assert response.status_code == 200
    body = response.json()
    assert [k["key"] for k in body["keys"]] == [k["key"] for k in SCHEMA["keys"]]
    max_loss = next(k for k in body["keys"] if k["key"] == "max_loss")
    assert (max_loss["off_allowed"], max_loss["live_reload"]) == (True, True)
    assert "aliases" not in max_loss
    assert body["lane_count"] == 3
    assert body["override_lanes"] == {"min": 2, "max": 10}
    assert body["split_choices"] == ["none", "budget", "bet", "both"]
    assert body["lane_split"] == "budget"
    assert body["base"] == {"max_loss": 0.02, "multiplier": 2.01, "proxy": None}
    assert body["lanes"] == {"2": {"multiplier": 3.0}}
    assert "leak" not in response.text
    assert "abcd" not in response.text
    assert ".wolfbet_autobot.json" not in response.text
    assert sorted(fake_agent.cmds()) == ["wolfbet.config_schema", "wolfbet.config_show"]


def test_viewers_and_other_services_get_nothing(signed_in_as: Any, fake_agent: FakeAgent) -> None:
    fake_agent.reply = _healthy
    viewer = signed_in_as(Role.VIEWER, two_factor=False)
    assert viewer.get("/wolfbet/config").status_code == 403
    assert _preview(viewer, CHANGES).status_code == 403
    admin = signed_in_as(Role.ADMIN)
    assert admin.get("/goated/config").status_code == 404
    assert fake_agent.calls == []


def test_a_preview_sends_pairs_built_on_the_server_and_returns_the_diff(
    signed_in_as: Any, fake_agent: FakeAgent
) -> None:
    fake_agent.reply = _healthy
    response = _preview(signed_in_as(Role.OPERATOR), CHANGES)
    assert response.status_code == 200
    assert response.json() == {
        "base": [{"key": "max_loss", "old": 0.02, "new": 0.01, "apply": "live", "removed": False}],
        "lanes": {
            "2": [
                {"key": "multiplier", "old": 3.0, "new": 4.0, "apply": "restart", "removed": False}
            ]
        },
        "warnings": [],
    }
    [call] = fake_agent.calls
    assert call["cmd"] == "wolfbet.config_dry_run"
    assert call["args"] == {"pairs": {"max_loss": "0.01", "lane2.multiplier": "4"}}


def test_a_refused_value_shows_the_bots_message(signed_in_as: Any, fake_agent: FakeAgent) -> None:
    refusal = {
        "v": 1,
        "error": {"code": "invalid_value", "message": "Mutation failed: Max loss must be a number"},
    }
    fake_agent.reply = lambda request: [ok(request, {"exit_code": 2, "json": refusal})]
    client = signed_in_as(Role.OPERATOR)
    response = _preview(client, {"changes": [{"key": "max_loss", "value": "abc", "lane": None}]})
    assert (response.status_code, response.json()) == (
        422,
        {"detail": "Max loss must be a number"},
    )
    fake_agent.reply = lambda request: [fail(request, "bad_request")]
    unknown = _preview(client, {"changes": [{"key": "nope", "value": "1", "lane": None}]})
    assert (unknown.status_code, unknown.json()) == (422, {"detail": INVALID_VALUE})
    fake_agent.reply = lambda request: [fail(request, "bad_request", key="proxy")]
    proxy = _preview(client, {"changes": [{"key": "proxy", "value": "direct", "lane": None}]})
    assert proxy.json() == {"detail": f"proxy: {INVALID_VALUE}"}
    fake_agent.reply = lambda request: [fail(request, "bad_request", key="<b>x</b>")]
    odd = _preview(client, {"changes": [{"key": "proxy", "value": "direct", "lane": None}]})
    assert odd.json() == {"detail": INVALID_VALUE}


def test_an_agent_failure_is_a_generic_502(signed_in_as: Any, fake_agent: FakeAgent) -> None:
    client = signed_in_as(Role.OPERATOR)
    fake_agent.reply = lambda request: [fail(request, "timeout")]
    assert client.get("/wolfbet/config").json() == {"detail": AGENT_DOWN}
    assert _preview(client, CHANGES).json() == {"detail": AGENT_DOWN}
    fake_agent.reply = lambda request: [ok(request, {"exit_code": 1, "stderr": "boom"})]
    broken = client.get("/wolfbet/config")
    assert (broken.status_code, broken.json()) == (502, {"detail": CONFIG_UNREADABLE})
    assert "boom" not in broken.text


def test_bad_changes_are_refused_before_the_agent(signed_in_as: Any, fake_agent: FakeAgent) -> None:
    client = signed_in_as(Role.OPERATOR)
    duplicate = {"changes": [CHANGES["changes"][0], CHANGES["changes"][0]]}
    base_lane = {"changes": [{"key": "multiplier", "value": "3", "lane": 1}]}
    assert _preview(client, duplicate).status_code == 422
    assert _preview(client, base_lane).status_code == 422
    assert _preview(client, {"changes": [], "pairs": {}}).status_code == 422
    long_value = {"changes": [{"key": "max_loss", "value": "1" * 201, "lane": None}]}
    assert _preview(client, long_value).status_code == 422
    too_many_rules = {"changes": [{"key": "custom_rules_text", "value": "x" * 4001, "lane": None}]}
    assert _preview(client, too_many_rules).status_code == 422
    assert fake_agent.calls == []


def test_custom_rules_may_be_longer_than_other_values(
    signed_in_as: Any, fake_agent: FakeAgent
) -> None:
    fake_agent.reply = _healthy
    rules = "[" + ", ".join(['{"n": 3}'] * 300) + "]"
    response = _preview(
        signed_in_as(Role.OPERATOR),
        {"changes": [{"key": "custom_rules_text", "value": rules, "lane": None}]},
    )
    assert response.status_code == 200
    assert fake_agent.calls[0]["args"] == {"pairs": {"custom_rules_text": rules}}
