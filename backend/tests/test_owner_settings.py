"""Settings every lane shares, and the account ones, are saved only by the owner."""

import ast
import secrets
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.wolfbet.control import OWNER_KEYS
from app.auth.models import Role
from tests.fake_agent import FakeAgent
from tests.test_config_api import _healthy
from tests.wolfbet_db import WORKSPACE

BOT = WORKSPACE / "wolfbet" / "wolfbet.py"


def _save(client: TestClient, changes: list[dict[str, Any]]) -> Any:
    token = client.get("/auth/me").json()["csrf_token"]
    headers = {"x-csrf-token": token, "idempotency-key": secrets.token_urlsafe(24)}
    return client.post("/wolfbet/control/config_set", json={"changes": changes}, headers=headers)


@pytest.mark.parametrize("role", [Role.OPERATOR, Role.ADMIN])
def test_others_are_refused_a_shared_or_account_setting(
    signed_in_as: Any, fake_agent: FakeAgent, role: Role
) -> None:
    client = signed_in_as(role)
    refused = _save(
        client,
        [{"key": "purge_days", "value": "7"}, {"key": "max_profit", "value": "0.01"}],
    )
    assert (refused.status_code, refused.json()["detail"]) == (
        403,
        "Only the owner can change purge_days.",
    )
    account = _save(client, [{"key": "currency", "value": "eth"}, {"key": "proxy", "value": "off"}])
    assert account.json()["detail"] == "Only the owner can change currency, proxy."
    assert fake_agent.calls == []


def test_others_keep_strategy_settings_and_lane_values(
    signed_in_as: Any, fake_agent: FakeAgent
) -> None:
    client = signed_in_as(Role.OPERATOR)
    assert _save(client, [{"key": "max_profit", "value": "0.01"}]).status_code == 200
    assert _save(client, [{"key": "currency", "value": "eth", "lane": 3}]).status_code == 200
    assert fake_agent.cmds() == ["wolfbet.config_set", "wolfbet.config_set"]


def test_the_owner_saves_them(signed_in_as: Any, fake_agent: FakeAgent) -> None:
    client = signed_in_as(Role.OWNER)
    changes = [
        {"key": "purge_days", "value": "7"},
        {"key": "events_url", "value": "redis://127.0.0.1:6379/0"},
    ]
    assert _save(client, changes).status_code == 200
    assert fake_agent.cmds() == ["wolfbet.config_set"]


def test_the_config_page_marks_them(signed_in_as: Any, fake_agent: FakeAgent) -> None:
    fake_agent.reply = _healthy
    keys = signed_in_as(Role.OPERATOR).get("/wolfbet/config").json()["keys"]
    marked = {k["key"] for k in keys if k["owner_only"]}
    assert marked == OWNER_KEYS & {k["key"] for k in keys}
    assert all(k["owner_only"] is False for k in keys if k["key"] not in OWNER_KEYS)


@pytest.mark.skipif(not BOT.is_file(), reason="needs the wolfbet checkout next to the dashboard")
def test_every_setting_the_lanes_share_is_owner_only() -> None:
    """The bot's own settings table names the shared ones; the raw key is never set here."""
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    table = next(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_SETTINGS" for t in node.targets)
    )
    settings: dict[str, tuple[str, bool]] = ast.literal_eval(table)
    shared = {key for key, (_, per_lane) in settings.items() if not per_lane} - {"api_key"}
    assert shared <= OWNER_KEYS
    assert OWNER_KEYS <= set(settings)
