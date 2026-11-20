"""deploy/backup.sh and deploy/restore.sh: encrypted dumps only, rotation, the timer, and the
guards on restoring.

docker, age, rclone, systemctl, apt-get and id are stubs that log their arguments; HOME is a
temp dir holding the config install.sh and `backup.sh configure` write.
"""

import os
import stat
import subprocess
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
RECIPIENT = "age1" + "q" * 58
PLAIN = "PGDMP plaintext dump"


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    path.chmod(0o755)


@pytest.fixture
def rig(tmp_path: Path) -> dict[str, Any]:
    home = tmp_path / "home"
    conf = home / ".config" / "casino-dashboard"
    conf.mkdir(parents=True)
    (conf / "dash.env").write_text("DASH_DIR=/x\nDASH_PROJECT=k8s0abc123def456\nDASH_HOST=h\n")
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
            ps) echo "${{STUB_DB_ID-ab12}}" ;;
            exec) printf '%s' '{PLAIN}'; exit "${{STUB_DUMP_EXIT:-0}}" ;;
        esac
        exit 0
        """,
    )
    _stub(
        bin_dir,
        "age",
        f"""\
        echo "age $*" >> {log}
        [ "${{STUB_AGE_EXIT:-0}}" = 0 ] || exit "$STUB_AGE_EXIT"
        printf 'age-encryption.org/v1 '; cat | tr 'a-z' 'A-Z'
        """,
    )
    _stub(
        bin_dir,
        "rclone",
        f"""\
        echo "rclone $*" >> {log}
        [ "$1" = listremotes ] && echo "b2:"
        [ "$1" = copy ] && exit "${{STUB_COPY_EXIT:-0}}"
        exit 0
        """,
    )
    _stub(bin_dir, "systemctl", f'echo "systemctl $*" >> {log}\n')
    _stub(bin_dir, "apt-get", f'echo "apt-get $*" >> {log}\n')
    return {"home": home, "conf": conf, "bin": bin_dir, "backups": tmp_path / "backups"}


def _run(
    rig: dict[str, Any], script: str, *args: str, **env: str
) -> subprocess.CompletedProcess[str]:
    full_env = {
        "HOME": str(rig["home"]),
        "PATH": f"{rig['bin']}{os.pathsep}{os.environ['PATH']}",
        "BACKUP_UNIT_DIR": str(rig["home"] / "units"),
        **env,
    }
    command = ["bash", str(DEPLOY / script), *args]
    return subprocess.run(  # noqa: S603  # nosec B603: our scripts under test, no shell
        command, capture_output=True, text=True, timeout=30, env=full_env
    )


def _configure(rig: dict[str, Any]) -> None:
    (rig["conf"] / "backup.env").write_text(
        f"AGE_RECIPIENT={RECIPIENT}\nRCLONE_REMOTE=b2:casino/wolfbet\nKEEP_DAYS=14\n"
        f"BACKUP_DIR={rig['backups']}\n"
    )


def _calls(rig: dict[str, Any]) -> list[str]:
    path = rig["home"] / "calls.log"
    return path.read_text().splitlines() if path.exists() else []


def test_run_keeps_only_the_encrypted_dump_and_copies_it_off_host(rig: dict[str, Any]) -> None:
    _configure(rig)
    result = _run(rig, "backup.sh", "run")
    assert result.returncode == 0, result.stderr
    files = list(rig["backups"].iterdir())
    assert len(files) == 1
    backup = files[0]
    assert backup.name.startswith("dashboard-") and backup.name.endswith("Z.dump.age")
    body = backup.read_text()
    assert body.startswith("age-encryption.org/v1") and PLAIN not in body
    assert stat.S_IMODE(rig["backups"].stat().st_mode) == 0o700
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    calls = _calls(rig)
    assert "docker exec ab12 pg_dump -U dashboard -Fc dashboard" in calls
    assert f"age -r {RECIPIENT}" in calls
    assert f"rclone copy {backup} b2:casino/wolfbet" in calls
    assert f"backup ok: {backup.name}" in result.stdout


@pytest.mark.parametrize("env", [{"STUB_DUMP_EXIT": "1"}, {"STUB_AGE_EXIT": "1"}])
def test_a_failed_dump_or_encryption_keeps_nothing(
    rig: dict[str, Any], env: dict[str, str]
) -> None:
    _configure(rig)
    result = _run(rig, "backup.sh", "run", **env)
    assert result.returncode == 1
    assert "backup failed: pg_dump or age did not finish" in result.stderr
    assert list(rig["backups"].iterdir()) == []
    assert not any(c.startswith("rclone copy") for c in _calls(rig))


def test_a_failed_copy_keeps_the_local_file(rig: dict[str, Any]) -> None:
    _configure(rig)
    result = _run(rig, "backup.sh", "run", STUB_COPY_EXIT="1")
    assert result.returncode == 1 and "the local file" in result.stderr
    assert len(list(rig["backups"].iterdir())) == 1


def test_rotation_removes_copies_older_than_two_weeks(rig: dict[str, Any]) -> None:
    _configure(rig)
    rig["backups"].mkdir(mode=0o700)
    old = rig["backups"] / "dashboard-20261101T033000Z.dump.age"
    recent = rig["backups"] / "dashboard-20261115T033000Z.dump.age"
    other = rig["backups"] / "notes.txt"
    for path, days in ((old, 16), (recent, 5), (other, 30)):
        path.write_text("x")
        stamp = time.time() - days * 86400
        os.utime(path, (stamp, stamp))
    assert _run(rig, "backup.sh", "run").returncode == 0
    assert not old.exists() and recent.exists() and other.exists()
    assert "rclone delete b2:casino/wolfbet --min-age 14d --include dashboard-*.dump.age" in _calls(
        rig
    )


def test_no_running_database_fails_cleanly(rig: dict[str, Any]) -> None:
    _configure(rig)
    result = _run(rig, "backup.sh", "run", STUB_DB_ID="")
    assert result.returncode == 1 and "is not running" in result.stderr


def test_unconfigured_backups_say_where_to_look(rig: dict[str, Any]) -> None:
    result = _run(rig, "backup.sh", "run")
    assert result.returncode == 2
    assert "Backups not configured: see docs/ops/restore.md" in result.stderr


def test_configure_saves_public_values_only(rig: dict[str, Any]) -> None:
    result = _run(
        rig, "backup.sh", "configure", "--recipient", RECIPIENT, "--remote", "b2:casino/wolfbet"
    )
    assert result.returncode == 0, result.stderr
    conf = rig["conf"] / "backup.env"
    assert stat.S_IMODE(conf.stat().st_mode) == 0o600
    assert conf.read_text().splitlines()[:2] == [
        f"AGE_RECIPIENT={RECIPIENT}",
        "RCLONE_REMOTE=b2:casino/wolfbet",
    ]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--recipient", "AGE-SECRET-KEY-1ABC", "--remote", "b2:x"], "age public key"),
        (["--recipient", RECIPIENT, "--remote", "b2:x; rm -rf /"], "NAME:PATH"),
        (["--recipient", RECIPIENT, "--remote", "s3:x"], "no remote named s3"),
    ],
)
def test_configure_refuses_bad_values(rig: dict[str, Any], args: list[str], message: str) -> None:
    result = _run(rig, "backup.sh", "configure", *args)
    assert result.returncode == 2 and message in result.stderr
    assert not (rig["conf"] / "backup.env").exists()


def test_install_writes_a_quiet_nightly_timer(rig: dict[str, Any]) -> None:
    result = _run(rig, "backup.sh", "install")
    assert result.returncode == 0, result.stderr
    units = rig["home"] / "units"
    service = (units / "casino-backup.service").read_text()
    timer = (units / "casino-backup.timer").read_text()
    assert f"ExecStart={DEPLOY}/backup.sh run" in service
    assert "IOSchedulingClass=idle" in service and "Nice=10" in service
    assert "OnCalendar=*-*-* 03:30" in timer and "Persistent=true" in timer
    assert "systemctl enable --now casino-backup.timer" in _calls(rig)
    assert _run(rig, "backup.sh", "install", STUB_UID="1000").returncode == 1


def test_restore_into_the_stack_needs_the_confirmation(rig: dict[str, Any], tmp_path: Path) -> None:
    backup, key = tmp_path / "b.dump.age", tmp_path / "key.txt"
    backup.write_text("x")
    key.write_text("AGE-SECRET-KEY-1TEST")
    result = _run(rig, "restore.sh", "into", str(backup), "--identity", str(key))
    assert result.returncode == 2 and "--yes-replace-dashboard-data" in result.stderr
    assert _calls(rig) == []


@pytest.mark.parametrize("missing", ["file", "identity"])
def test_restore_needs_the_file_and_the_key(
    rig: dict[str, Any], tmp_path: Path, missing: str
) -> None:
    backup, key = tmp_path / "b.dump.age", tmp_path / "key.txt"
    if missing != "file":
        backup.write_text("x")
    if missing != "identity":
        key.write_text("AGE-SECRET-KEY-1TEST")
    result = _run(rig, "restore.sh", "drill", str(backup), "--identity", str(key))
    assert result.returncode == 2
    assert _calls(rig) == []
