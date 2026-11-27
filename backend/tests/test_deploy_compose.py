"""Deployment invariants: caps, isolation, secrets and the Redis ACL."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
SERVICES = ("timescale", "redis", "api", "ingest", "mailer", "migrate", "caddy")
# One-shot: runs to completion before the app services on every deploy, never restarted.
TOOLS = ("migrate",)
NEEDS_MIGRATION = ("api", "ingest", "mailer")
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
        assert "profiles" not in service
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
    if name == "ingest":
        # Root only to read the root-owned bot database, and only with every mount read-only.
        assert all(str(v).endswith(":ro") for v in service["volumes"])
    else:
        assert not str(service["user"]).startswith("0")
    assert service["networks"] == (["internal", "egress"] if name == "mailer" else ["internal"])


def test_nothing_is_public_and_redis_stays_on_loopback(compose: dict[str, Any]) -> None:
    """The host nginx owns 80/443 and reaches caddy on loopback; Redis is on loopback for the
    bot daemon. Nothing else is published, and the defaults never leave 127.0.0.1."""
    expected = {
        "redis": ["${REDIS_PUBLISH:-127.0.0.1:9479:6379}"],
        "caddy": ["${CADDY_PUBLISH:-127.0.0.1:9441:8080}"],
    }
    for name in SERVICES:
        ports = _service(compose, name).get("ports", [])
        assert ports == expected.get(name, []), f"{name} publishes {ports}"


@pytest.mark.parametrize("name", NEEDS_MIGRATION)
def test_app_services_start_only_after_migrations(compose: dict[str, Any], name: str) -> None:
    depends = _service(compose, name)["depends_on"]
    assert depends["migrate"] == {"condition": "service_completed_successfully"}


def test_caddy_image_carries_the_spa(compose: dict[str, Any]) -> None:
    caddy = _service(compose, "caddy")
    assert caddy["build"] == {
        "context": "../frontend",
        "dockerfile": "../deploy/caddy/Dockerfile",
        "additional_contexts": {"deploy": "."},
    }
    assert not any("frontend/build" in str(v) for v in caddy["volumes"])
    dockerfile = (DEPLOY / "caddy" / "Dockerfile").read_text()
    assert "pnpm install --frozen-lockfile" in dockerfile
    assert "COPY --from=build /app/build /srv" in dockerfile
    assert "FROM caddy:2.11.9-alpine" in dockerfile


def test_internal_network_has_no_route_out(compose: dict[str, Any]) -> None:
    assert compose["networks"]["internal"]["internal"] is True
    for name in ("timescale", "api", "ingest"):
        assert "edge" not in _service(compose, name)["networks"]


def test_only_the_mailer_reaches_out_and_holds_the_mail_and_alert_bot_keys(
    compose: dict[str, Any],
) -> None:
    for name in SERVICES:
        service = _service(compose, name)
        has_egress = "egress" in service.get("networks", [])
        has_key = "brevo_api_key" in _secrets(service)
        assert has_egress == (name == "mailer"), name
        assert has_key == (name == "mailer"), name
    assert _secrets(_service(compose, "mailer")) == {
        "postgres_app_password": "postgres_password",
        "brevo_api_key": "brevo_api_key",
        "telegram_bot_token": "telegram_bot_token",
        "vapid_private_key": "vapid_private_key",
    }
    for name in SERVICES:
        if name != "mailer":
            assert "telegram_bot_token" not in _secrets(_service(compose, name)), name
            assert "vapid_private_key" not in _secrets(_service(compose, name)), name


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
        if SECRET_WORDS.search(key) and not key.endswith("_PUBLIC_KEY"):
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
        "agent_token": "agent_token",
        "tg_login_secret": "tg_login_secret",
    }
    env = api["environment"]
    assert env["APP_ENV"] == "production"
    assert env["PUBLIC_HOST"].startswith("${WOLFBET_HOST:?")
    assert env["POSTGRES_HOST"] == "timescale"
    assert env["REDIS_HOST"] == "redis"
    assert env["LIVE_USER_SERVICE"] == "${LIVE_USER_SERVICE:-}"


def test_api_reaches_the_agent_only_through_its_group_and_a_read_only_mount(
    compose: dict[str, Any],
) -> None:
    api = _service(compose, "api")
    assert api["group_add"] == ["${AGENT_GID:?set AGENT_GID in deploy/.env}"]
    assert api["volumes"][0] == "${AGENT_RUNTIME_DIR:-/run/casino-agent}:/run/casino-agent:ro"
    assert api["environment"]["AGENT_SOCKET"] == "/run/casino-agent/agent.sock"
    others = [name for name in SERVICES if name != "api"]
    assert not any("agent_token" in _secrets(_service(compose, name)) for name in others)


def test_api_is_served_by_the_factory() -> None:
    dockerfile = (DEPLOY.parent / "backend" / "Dockerfile").read_text()
    assert '"app.main:create_app", "--factory"' in dockerfile


def test_api_caps_websocket_frames_and_pings_idle_sockets() -> None:
    dockerfile = (DEPLOY.parent / "backend" / "Dockerfile").read_text()
    assert '"--ws-max-size", "16384"' in dockerfile
    assert '"--ws-ping-interval", "20", "--ws-ping-timeout", "20"' in dockerfile


def test_secrets_come_from_files(compose: dict[str, Any]) -> None:
    for name, secret in compose["secrets"].items():
        assert secret["file"] == f"${{SECRETS_DIR:-./secrets}}/{name}.txt"


def test_ingest_reads_the_bot_directory_read_only(compose: dict[str, Any]) -> None:
    mounts = _service(compose, "ingest")["volumes"]
    bot = [m for m in mounts if m.endswith(":/botdata:ro")]
    assert len(bot) == 1
    assert "BOT_DATA_DIR" in bot[0]
    assert not bot[0].split(":")[0].endswith(".db")


def test_ingest_takes_the_telegram_data_dir_from_env_and_defaults_to_none(
    compose: dict[str, Any],
) -> None:
    environment = _service(compose, "ingest")["environment"]
    assert environment["INGEST_TG_DATA_DIR"] == "${INGEST_TG_DATA_DIR:-}"
    example = (DEPLOY / "env.example").read_text()
    assert "\nINGEST_TG_DATA_DIR=\n" in example


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
        assert {t for t in rule.split() if t.startswith("~")} == {"~wolfbet:*", "~wolfbet-tg:*"}

    def test_dashboard_reads_the_cli_and_telegram_streams(self, acl: dict[str, str]) -> None:
        root = acl["dashboard"].partition("(")[0]
        assert {t for t in root.split() if t.startswith("~")} == {"~wolfbet:*", "~wolfbet-tg:*"}

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


def test_caddy_serves_plain_http_behind_the_proxy_and_keeps_the_client_address() -> None:
    caddyfile = (DEPLOY / "Caddyfile").read_text()
    assert "{$CADDY_ADDRESS::80} {" in caddyfile
    assert "trusted_proxies static private_ranges" in caddyfile


def test_metrics_are_not_served_through_caddy() -> None:
    caddyfile = (DEPLOY / "Caddyfile").read_text()
    assert caddyfile.index("respond /api/metrics 404") < caddyfile.index("handle_path /api/*")


def test_the_coolify_variant_mounts_the_agent_directory_the_system_unit_creates() -> None:
    """casino-agent.service is a system unit: RuntimeDirectory= puts its socket in /run."""
    coolify: dict[str, Any] = yaml.safe_load((DEPLOY / "compose.coolify.yml").read_text())
    unit = (DEPLOY.parent / "agent" / "systemd" / "casino-agent.service").read_text()
    assert "RuntimeDirectory=casino-agent" in unit
    assert _service(coolify, "api")["volumes"][0] == "/run/casino-agent:/run/casino-agent:ro"


@pytest.fixture(scope="module")
def coolify() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((DEPLOY / "compose.coolify.yml").read_text())
    return data


def test_the_coolify_variant_runs_the_same_services(coolify: dict[str, Any]) -> None:
    assert set(coolify["services"]) == set(SERVICES)


def test_the_coolify_variant_pulls_every_image_from_a_registry(coolify: dict[str, Any]) -> None:
    """Coolify pulls before it starts anything and never builds from the repo."""
    app = re.compile(r"ghcr\.io/snad1/casino-dashboard-(api|caddy|redis):\$\{IMAGE_TAG:-latest\}")
    upstream = re.compile(r"[a-z0-9/._-]+:\d[\w.-]*")
    for name, service in coolify["services"].items():
        assert "build" not in service, name
        image = service["image"]
        assert app.fullmatch(image) or upstream.fullmatch(image), f"{name}: {image}"
        assert not image.endswith(":local"), name


def test_the_coolify_variant_binds_only_absolute_host_paths(coolify: dict[str, Any]) -> None:
    """Coolify copies no repo file, so a relative bind would mount an empty directory."""
    named = set(coolify.get("volumes", {}))
    for name, service in coolify["services"].items():
        for mount in service.get("volumes", []):
            source = mount.split(":", 1)[0]
            assert "${" not in source, f"{name}: {mount}"
            assert source in named or source.startswith("/"), f"{name}: {mount}"
    for name, secret in coolify["secrets"].items():
        assert secret["file"].startswith("/opt/wolfbet/secrets/"), name


def test_the_coolify_variant_keeps_the_loopback_ports(coolify: dict[str, Any]) -> None:
    published = {name: s.get("ports") for name, s in coolify["services"].items() if s.get("ports")}
    assert published == {"redis": ["127.0.0.1:9479:6379"], "caddy": ["127.0.0.1:9441:8080"]}


def test_coolify_ignores_the_finished_migration_and_waits_for_it(coolify: dict[str, Any]) -> None:
    assert _service(coolify, "migrate")["exclude_from_hc"] is True
    for name in NEEDS_MIGRATION:
        wait = _service(coolify, name)["depends_on"]["migrate"]
        assert wait == {"condition": "service_completed_successfully"}, name


def test_the_config_files_coolify_cannot_copy_are_baked_into_the_images() -> None:
    caddy = (DEPLOY / "caddy" / "Dockerfile").read_text()
    assert "COPY --from=deploy Caddyfile /etc/caddy/Caddyfile" in caddy
    redis = (DEPLOY / "redis" / "Dockerfile").read_text()
    assert "FROM redis:7.4.12-alpine" in redis
    assert "COPY entrypoint.sh users.acl.tmpl /etc/redis/" in redis


@pytest.mark.parametrize("file", ["compose.yml", "compose.coolify.yml"])
def test_every_container_that_reads_a_service_gets_the_service_list(file: str) -> None:
    data: dict[str, Any] = yaml.safe_load((DEPLOY / file).read_text())
    for name in ("redis", "api", "ingest"):
        assert _service(data, name)["environment"]["DASHBOARD_SERVICES"] == (
            "${DASHBOARD_SERVICES:-wolfbet}"
        ), name
    assert "\nDASHBOARD_SERVICES=wolfbet\n" in (DEPLOY / "env.example").read_text()


@pytest.mark.parametrize(
    ("file", "source"),
    [
        ("compose.yml", "${SECRETS_DIR:-./secrets}/publishers"),
        ("compose.coolify.yml", "/opt/wolfbet/secrets/publishers"),
    ],
)
def test_only_redis_mounts_the_publisher_passwords_read_only(file: str, source: str) -> None:
    data: dict[str, Any] = yaml.safe_load((DEPLOY / file).read_text())
    mounts = {
        name: [v for v in service.get("volumes", []) if "/run/publishers" in str(v)]
        for name, service in data["services"].items()
    }
    assert mounts.pop("redis") == [f"{source}:/run/publishers:ro"]
    assert all(not found for found in mounts.values()), mounts


@pytest.mark.parametrize(
    ("file", "source"),
    [
        ("compose.yml", "${SECRETS_DIR:-./secrets}/hooks"),
        ("compose.coolify.yml", "/opt/wolfbet/secrets/hooks"),
    ],
)
def test_only_the_api_mounts_the_webhook_secret_read_only(file: str, source: str) -> None:
    data: dict[str, Any] = yaml.safe_load((DEPLOY / file).read_text())
    mounts = {
        name: [v for v in service.get("volumes", []) if "/run/hooks" in str(v)]
        for name, service in data["services"].items()
    }
    assert mounts.pop("api") == [f"{source}:/run/hooks:ro"]
    assert all(not found for found in mounts.values()), mounts
