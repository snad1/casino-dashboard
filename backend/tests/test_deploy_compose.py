"""Deployment invariants: caps, isolation, secrets and the Redis ACL."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
SERVICES = ("timescale", "redis", "api", "ingest", "mailer", "migrate", "caddy")
# One-shot tools: started by hand with `docker compose run --rm`, never restarted.
TOOLS = ("migrate",)
CAPS = {
    "timescale": (1.0, "1g"),
    "redis": (0.25, "320m"),
    "api": (1.0, "512m"),
    "ingest": (0.5, "256m"),
    "mailer": (0.25, "128m"),
    "migrate": (0.5, "256m"),
    "caddy": (0.25, "128m"),
}
SECRET_WORDS = re.compile(r"PASSWORD|SECRET|TOKEN|KEY", re.IGNORECASE)


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((DEPLOY / "compose.yml").read_text())
    return data


def _service(compose: dict[str, Any], name: str) -> dict[str, Any]:
    service: dict[str, Any] = compose["services"][name]
    return service


def _secrets(service: dict[str, Any]) -> dict[str, str]:
    """Each mounted secret as {source: name it has inside the container}."""
    mounted: dict[str, str] = {}
    for entry in service.get("secrets", []):
        if isinstance(entry, str):
            mounted[entry] = entry
        else:
            mounted[entry["source"]] = entry.get("target", entry["source"])
    return mounted


def test_exactly_the_planned_services(compose: dict[str, Any]) -> None:
    assert set(compose["services"]) == set(SERVICES)


@pytest.mark.parametrize("name", SERVICES)
def test_every_service_is_capped_and_hardened(compose: dict[str, Any], name: str) -> None:
    service = _service(compose, name)
    cpus, mem = CAPS[name]
    assert service["cpus"] == cpus
    assert service["mem_limit"] == mem
    assert service["cpu_shares"] == 256
    assert "no-new-privileges:true" in service["security_opt"]
    if name in TOOLS:
        assert service["restart"] == "no"
        assert service["profiles"] == ["tools"]
    else:
        assert service["restart"] == "unless-stopped"
        assert "test" in service["healthcheck"]


@pytest.mark.parametrize("name", ["api", "ingest", "mailer", "migrate"])
def test_app_containers_are_locked_down(compose: dict[str, Any], name: str) -> None:
    service = _service(compose, name)
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "cap_add" not in service
    assert any(t.startswith("/tmp") for t in service["tmpfs"])  # noqa: S108
    assert not str(service["user"]).startswith("0")
    assert service["networks"] == (["internal", "egress"] if name == "mailer" else ["internal"])


def test_only_caddy_is_public(compose: dict[str, Any]) -> None:
    for name in SERVICES:
        for port in _service(compose, name).get("ports", []):
            if name == "caddy":
                assert str(port).split(":")[0] in {"80", "443"}
            else:
                assert str(port).startswith("127.0.0.1:"), f"{name} publishes {port}"


def test_internal_network_has_no_route_out(compose: dict[str, Any]) -> None:
    assert compose["networks"]["internal"]["internal"] is True
    for name in ("timescale", "api", "ingest"):
        assert "edge" not in _service(compose, name)["networks"]


def test_only_the_mailer_reaches_out_and_holds_the_mail_key(compose: dict[str, Any]) -> None:
    for name in SERVICES:
        service = _service(compose, name)
        has_egress = "egress" in service.get("networks", [])
        has_key = "brevo_api_key" in _secrets(service)
        assert has_egress == (name == "mailer"), name
        assert has_key == (name == "mailer"), name
    assert _secrets(_service(compose, "mailer")) == {
        "postgres_app_password": "postgres_password",
        "brevo_api_key": "brevo_api_key",
    }


def test_only_migrate_holds_the_database_owner_password(compose: dict[str, Any]) -> None:
    for name in SERVICES:
        if name in ("timescale", "migrate"):
            continue
        assert "postgres_password" not in _secrets(_service(compose, name)), name
    migrate = _service(compose, "migrate")
    assert migrate["environment"]["POSTGRES_USER"] == "dashboard"
    assert set(_secrets(migrate)) == {"postgres_password", "postgres_app_password"}
    for name in ("api", "mailer"):
        assert _service(compose, name)["environment"]["POSTGRES_USER"] == "dashboard_app"


@pytest.mark.parametrize("name", SERVICES)
def test_no_secret_values_inline(compose: dict[str, Any], name: str) -> None:
    env = _service(compose, name).get("environment", {})
    for key, value in env.items():
        if SECRET_WORDS.search(key):
            assert key.endswith("_FILE") and str(value).startswith("/run/secrets/")


def test_api_gets_its_secrets_as_files_and_plain_settings_as_env(
    compose: dict[str, Any],
) -> None:
    api = _service(compose, "api")
    assert _secrets(api) == {
        "session_secret": "session_secret",
        "totp_key": "totp_key",
        "postgres_app_password": "postgres_password",
        "redis_dashboard_password": "redis_dashboard_password",
    }
    env = api["environment"]
    assert env["APP_ENV"] == "production"
    assert env["PUBLIC_HOST"].startswith("${WOLFBET_HOST:?")
    assert env["POSTGRES_HOST"] == "timescale"
    assert env["REDIS_HOST"] == "redis"


def test_api_is_served_by_the_factory() -> None:
    dockerfile = (DEPLOY.parent / "backend" / "Dockerfile").read_text()
    assert '"app.main:create_app", "--factory"' in dockerfile


def test_api_caps_websocket_frames_and_pings_idle_sockets() -> None:
    dockerfile = (DEPLOY.parent / "backend" / "Dockerfile").read_text()
    assert '"--ws-max-size", "16384"' in dockerfile
    assert '"--ws-ping-interval", "20", "--ws-ping-timeout", "20"' in dockerfile


def test_secrets_come_from_files(compose: dict[str, Any]) -> None:
    for secret in compose["secrets"].values():
        assert secret["file"].startswith("./secrets/")


def test_ingest_reads_the_bot_directory_read_only(compose: dict[str, Any]) -> None:
    mounts = _service(compose, "ingest")["volumes"]
    bot = [m for m in mounts if m.endswith(":/botdata:ro")]
    assert len(bot) == 1
    assert "BOT_DATA_DIR" in bot[0]
    assert not bot[0].split(":")[0].endswith(".db")


def test_secrets_directory_is_ignored_by_git() -> None:
    ignored = (DEPLOY.parent / ".gitignore").read_text().splitlines()
    assert "deploy/secrets/" in ignored
    assert "backend/.secrets/" in ignored
    assert ".env" in ignored


@pytest.fixture(scope="module")
def acl() -> dict[str, str]:
    rules: dict[str, str] = {}
    for line in (DEPLOY / "redis" / "users.acl.tmpl").read_text().splitlines():
        if line.startswith("user "):
            _, name, rest = line.split(" ", 2)
            rules[name] = rest
    return rules


class TestRedisAcl:
    def test_default_user_is_off(self, acl: dict[str, str]) -> None:
        assert acl["default"].strip() == "off"

    def test_publisher_can_only_append_events(self, acl: dict[str, str]) -> None:
        rule = acl["publisher"]
        assert "-@all" in rule
        commands = {t for t in rule.split() if t.startswith("+")}
        assert commands == {"+xadd", "+ping"}
        assert "~wolfbet:*" in rule

    def test_dashboard_cannot_write_streams(self, acl: dict[str, str]) -> None:
        root, _, selector = acl["dashboard"].partition("(")
        assert "-@all" in root
        for write in ("+xadd", "+del", "+set", "+zadd", "+@write", "+@all", "+flushall"):
            assert write not in root.split()
        assert selector.rstrip().rstrip(")").split() == [
            "~auth:*",
            "+zadd",
            "+zremrangebyscore",
            "+zcard",
            "+pexpire",
        ]

    def test_no_password_in_template(self, acl: dict[str, str]) -> None:
        for rule in acl.values():
            assert ">" not in rule
            assert "nopass" not in rule

    def test_entrypoint_refuses_missing_secrets(self) -> None:
        script = (DEPLOY / "redis" / "entrypoint.sh").read_text()
        assert "refusing to start" in script
        assert '--save ""' in script
        assert "--maxmemory 256mb" in script


def test_caddy_sends_security_headers() -> None:
    caddyfile = (DEPLOY / "Caddyfile").read_text()
    for header in (
        "Strict-Transport-Security",
        "Content-Security-Policy",
        "X-Content-Type-Options",
        "frame-ancestors 'none'",
        "-Server",
    ):
        assert header in caddyfile
    assert "admin off" in caddyfile
