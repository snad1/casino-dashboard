"""deploy/install.sh: secrets made once and never printed, the firewall keeps SSH and opens
only 80/443, Redis and Postgres never public, the Coolify env block and the owner step.

The script runs from a copy of the deploy tree with a fake agent installer, a temp HOME and
stubbed host tools (docker, ufw, sshd, ss, getent, ...) that log their arguments; openssl is
the real one.
"""

import os
import re
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "install.sh"
HOST = "wolfbet.example.com"
RANDOM_SECRETS = (
    "session_secret",
    "totp_key",
    "postgres_password",
    "postgres_app_password",
    "redis_publisher_password",
    "redis_dashboard_password",
)
BREVO = "xkeysib-test-0123456789abcdef"
AGENT_TOKEN = "agent-token-0123456789abcdefghijklmnopqrstuvwxyz"  # noqa: S105  # nosec B105: test value


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    path.chmod(0o755)


@pytest.fixture
def rig(tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "casino-dashboard"
    (root / "deploy").mkdir(parents=True)
    (root / "agent").mkdir()
    shutil.copy(SCRIPT, root / "deploy" / "install.sh")
    (root / "agent" / "install.sh").write_text(
        textwrap.dedent(f"""\
            echo "agent install uid=$CASINO_AGENT_API_UID" >> "$HOME/calls.log"
            mkdir -p "$HOME/.config/casino-agent"
            printf '%s' '{AGENT_TOKEN}' > "$HOME/.config/casino-agent/token"
            """)
    )
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = '"$HOME/calls.log"'
    _stub(bin_dir, "id", 'echo "${STUB_UID:-0}"\n')
    _stub(
        bin_dir,
        "docker",
        f"""\
        echo "docker $*" >> {log}
        case "$1" in
            ps) echo "${{STUB_API_ID:-}}" ;;
            exec) printf '%s\\n' "${{STUB_OWNER_OUT:-Owner created}}"
                  exit "${{STUB_OWNER_EXIT:-0}}" ;;
        esac
        exit 0
        """,
    )
    _stub(bin_dir, "ufw", f'echo "ufw $*" >> {log}\n')
    _stub(bin_dir, "apt-get", f'echo "apt-get $*" >> {log}\n')
    _stub(bin_dir, "uv", "exit 0\n")
    _stub(bin_dir, "sshd", '[ -n "${STUB_NO_SSH:-}" ] || echo "port ${STUB_SSH_PORT:-2222}"\n')
    _stub(
        bin_dir,
        "ss",
        'case "$1" in *p*) exit 0 ;; esac\n'
        'printf "%b" "${STUB_LISTEN:-}" | while read -r a; do echo "LISTEN 0 4096 $a *:*"; done\n',
    )
    _stub(
        bin_dir,
        "getent",
        """\
        case "$1" in
            group) echo "casino-agent:x:998:" ;;
            ahosts) [ -n "${STUB_DNS:-}" ] && echo "$STUB_DNS STREAM $2" ;;
        esac
        exit 0
        """,
    )
    _stub(bin_dir, "hostname", 'echo "203.0.113.9 10.0.0.2"\n')
    _stub(bin_dir, "timedatectl", "echo Europe/Berlin\n")
    brevo = tmp_path / "brevo.txt"
    brevo.write_text(BREVO)
    return {"root": root, "home": home, "bin": bin_dir, "brevo": brevo}


def _run(rig: dict[str, Any], *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    full_env = {
        "HOME": str(rig["home"]),
        "PATH": f"{rig['bin']}{os.pathsep}{os.environ['PATH']}",
        "STUB_LISTEN": "0.0.0.0:22\\n127.0.0.1:6379\\n",
        **env,
    }
    script = str(rig["root"] / "deploy" / "install.sh")
    return subprocess.run(  # noqa: S603  # nosec B603: our script under test, no shell
        ["bash", script, *args],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=60,
        env=full_env,
        stdin=subprocess.DEVNULL,
    )


def _install(rig: dict[str, Any], *extra: str, **env: str) -> subprocess.CompletedProcess[str]:
    return _run(
        rig,
        "--host",
        HOST,
        "--mail-from",
        "no-reply@example.com",
        "--brevo-key-file",
        str(rig["brevo"]),
        *extra,
        **env,
    )


def _calls(rig: dict[str, Any]) -> list[str]:
    path = rig["home"] / "calls.log"
    return path.read_text().splitlines() if path.exists() else []


def _secrets(rig: dict[str, Any]) -> Path:
    path: Path = rig["root"] / "deploy" / "secrets"
    return path


def test_install_makes_every_secret_once_and_never_prints_one(rig: dict[str, Any]) -> None:
    result = _install(rig)
    assert result.returncode == 0, result.stderr
    secrets = _secrets(rig)
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o700
    values = {}
    for name in RANDOM_SECRETS:
        value = (secrets / f"{name}.txt").read_text()
        assert len(value) >= 48 and "\n" not in value
        values[name] = value
    assert len(set(values.values())) == len(RANDOM_SECRETS)
    assert (secrets / "agent_token.txt").read_text() == AGENT_TOKEN
    assert (secrets / "brevo_api_key.txt").read_text() == BREVO
    assert (secrets / "telegram_bot_token.txt").read_text() == ""
    assert "PRIVATE KEY" in (secrets / "vapid_private_key.txt").read_text()
    for file in secrets.iterdir():
        assert stat.S_IMODE(file.stat().st_mode) == 0o644, file.name
    output = result.stdout + result.stderr
    for value in [*values.values(), AGENT_TOKEN, BREVO]:
        assert value not in output
    assert "agent install uid=10001" in _calls(rig)

    again = _install(rig)
    assert again.returncode == 0, again.stderr
    for name, value in values.items():
        assert (secrets / f"{name}.txt").read_text() == value
    assert "created" not in again.stdout


def test_firewall_keeps_ssh_and_opens_only_web_ports(rig: dict[str, Any]) -> None:
    assert _install(rig, STUB_SSH_PORT="2222").returncode == 0
    ufw = [c for c in _calls(rig) if c.startswith("ufw ")]
    assert ufw == [
        "ufw default deny incoming",
        "ufw default allow outgoing",
        "ufw allow 2222/tcp",
        "ufw allow 80/tcp",
        "ufw allow 443/tcp",
        "ufw allow 443/udp",
        "ufw --force enable",
    ]


def test_unknown_ssh_port_leaves_the_firewall_alone(rig: dict[str, Any]) -> None:
    result = _install(rig, STUB_NO_SSH="1")
    assert result.returncode == 1
    assert "could not find the SSH port" in result.stderr
    assert not any(c.startswith("ufw ") for c in _calls(rig))


@pytest.mark.parametrize("listen", ["0.0.0.0:6379", "[::]:5432", "203.0.113.9:6379"])
def test_public_redis_or_postgres_stops_the_install(rig: dict[str, Any], listen: str) -> None:
    result = _install(rig, STUB_LISTEN=f"0.0.0.0:22\\n{listen}\\n")
    assert result.returncode == 1
    assert "must never be public" in result.stderr
    assert not any(c.startswith("ufw ") for c in _calls(rig))


def test_env_block_and_config(rig: dict[str, Any]) -> None:
    result = _install(
        rig,
        "--coolify-url",
        "https://coolify.example.com/",
        "--coolify-app",
        "k8s0abc123def456",
        STUB_DNS="203.0.113.9",
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for line in (
        f"WOLFBET_HOST={HOST}",
        "MAIL_FROM=no-reply@example.com",
        "BOT_DATA_DIR=/root",
        "HOST_TZ=Europe/Berlin",
        "AGENT_GID=998",
        "AGENT_RUNTIME_DIR=/run/user/0/casino-agent",
        f"SECRETS_DIR={_secrets(rig)}",
        "CADDY_ADDRESS=:80",
    ):
        assert line in out.splitlines(), line
    vapid = re.search(r"^VAPID_PUBLIC_KEY=([A-Za-z0-9_-]{87})$", out, re.MULTILINE)
    assert vapid is not None
    assert f"DNS: {HOST} points here." in out
    conf = rig["home"] / ".config" / "casino-dashboard" / "dash.env"
    assert stat.S_IMODE(conf.stat().st_mode) == 0o600
    assert conf.read_text().splitlines() == [
        f"DASH_DIR={rig['root']}",
        "DASH_PROJECT=k8s0abc123def456",
        f"DASH_HOST={HOST}",
        "COOLIFY_URL=https://coolify.example.com",
    ]


def test_dns_elsewhere_is_only_a_warning(rig: dict[str, Any]) -> None:
    result = _install(rig, STUB_DNS="198.51.100.7")
    assert result.returncode == 0
    assert "resolves to 198.51.100.7" in result.stderr


def test_dry_run_changes_nothing(rig: dict[str, Any]) -> None:
    result = _install(rig, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert not _secrets(rig).exists()
    assert not (rig["home"] / ".config").exists()
    assert "would run: ufw --force enable" in result.stdout
    assert not any(c.startswith(("ufw ", "agent ")) for c in _calls(rig))


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--host", "not a host", "--mail-from", "a@b.co"], "--host must be a hostname"),
        (["--host", HOST, "--mail-from", "nobody"], "--mail-from must be an email"),
        (["--host", HOST, "--mail-from", "a@b.co", "--coolify-app", "../x"], "--coolify-app"),
        (["--host", HOST, "--mail-from", "a@b.co", "--bot-data-dir", "root"], "absolute"),
    ],
)
def test_bad_arguments_change_nothing(rig: dict[str, Any], args: list[str], message: str) -> None:
    result = _run(rig, *args)
    assert result.returncode == 2
    assert message in result.stderr
    assert _calls(rig) == []


def test_needs_root(rig: dict[str, Any]) -> None:
    result = _install(rig, STUB_UID="1000")
    assert result.returncode == 1 and "run as root" in result.stderr


def test_missing_docker_points_to_coolify(rig: dict[str, Any]) -> None:
    (rig["bin"] / "docker").unlink()
    path = f"{rig['bin']}{os.pathsep}/usr/bin{os.pathsep}/bin"
    if shutil.which("docker", path=path):
        pytest.skip("a real docker is on the minimal PATH")
    result = _install(rig, PATH=path)
    assert result.returncode == 1 and "Install Coolify first" in result.stderr


def test_owner_step(rig: dict[str, Any]) -> None:
    assert _install(rig, "--coolify-app", "k8s0abc123def456").returncode == 0
    made = _run(rig, "--owner-email", "me@example.com", STUB_API_ID="c0ffee")
    assert made.returncode == 0, made.stderr
    assert "docker exec -it c0ffee python -m app.cli create-owner --email me@example.com" in _calls(
        rig
    )
    exists = _run(
        rig,
        "--owner-email",
        "me@example.com",
        STUB_API_ID="c0ffee",
        STUB_OWNER_OUT="An owner already exists.",
        STUB_OWNER_EXIT="1",
    )
    assert exists.returncode == 0 and "the owner exists" in exists.stdout
    down = _run(rig, "--owner-email", "me@example.com", STUB_API_ID="")
    assert down.returncode == 1 and "deploy the resource in Coolify first" in down.stderr


def test_owner_step_needs_the_install_first(rig: dict[str, Any]) -> None:
    result = _run(rig, "--owner-email", "me@example.com")
    assert result.returncode == 2 and "run the install first" in result.stderr
