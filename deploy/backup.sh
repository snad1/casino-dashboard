#!/usr/bin/env bash
# Nightly backup of the dashboard database: encrypted, copied off the host, kept 14 days.
#
#   deploy/backup.sh configure --recipient age1... --remote NAME:PATH   once, as root
#   deploy/backup.sh install                                           once: timer at 03:30
#   deploy/backup.sh run                                               what the timer runs
#
# `pg_dump -Fc` of the stack's database streams through `age` to the public recipient, so
# no unencrypted dump ever touches the disk and the host cannot read its own backups: the
# private key stays with the owner. Each file is copied to the rclone remote, and copies
# older than KEEP_DAYS are deleted here and there. See docs/ops/restore.md.
set -euo pipefail
umask 077

DEPLOY="$(cd "$(dirname "$0")" && pwd)"
CONF_DIR="$HOME/.config/casino-dashboard"
CONF="$CONF_DIR/backup.env"
UNIT_DIR="${BACKUP_UNIT_DIR:-/etc/systemd/system}"
PATTERN="dashboard-*.dump.age"

die() { echo "backup: $1" >&2; exit "${2:-1}"; }
fail() { echo "backup failed: $1" >&2; exit 1; }
usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

valid_recipient() { [[ "$1" =~ ^age1[0-9a-z]{58}$ ]]; }
valid_remote() { [[ "$1" =~ ^:?[A-Za-z0-9_.-]+:[A-Za-z0-9_./-]*$ ]]; }

load() {
    [ -f "$CONF" ] || die "Backups not configured: see docs/ops/restore.md" 2
    [ -f "$CONF_DIR/dash.env" ] || die "Dashboard not installed: run deploy/install.sh" 2
    AGE_RECIPIENT="" RCLONE_REMOTE="" KEEP_DAYS=14 BACKUP_DIR=/var/backups/casino-dashboard
    DASH_PROJECT=""
    # shellcheck disable=SC1091
    . "$CONF"
    # shellcheck disable=SC1091
    . "$CONF_DIR/dash.env"
    valid_recipient "$AGE_RECIPIENT" || die "AGE_RECIPIENT in $CONF is not an age public key" 2
    valid_remote "$RCLONE_REMOTE" || die "RCLONE_REMOTE in $CONF is not an rclone remote" 2
    [[ "$KEEP_DAYS" =~ ^[0-9]{1,3}$ ]] || die "KEEP_DAYS in $CONF must be a number" 2
    [ -n "$DASH_PROJECT" ] || die "no Coolify app in dash.env: re-run install.sh with --coolify-app" 2
}

configure() {
    local recipient="" remote=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --recipient) recipient="${2:-}"; shift 2 ;;
            --remote) remote="${2:-}"; shift 2 ;;
            *) usage ;;
        esac
    done
    valid_recipient "$recipient" || die "--recipient must be an age public key (age1...)" 2
    valid_remote "$remote" || die "--remote must look like NAME:PATH" 2
    if [[ "$remote" != :* ]] && ! rclone listremotes | grep -qx "${remote%%:*}:"; then
        die "rclone has no remote named ${remote%%:*}: run rclone config first" 2
    fi
    install -d -m 0700 "$CONF_DIR"
    cat > "$CONF" <<EOF
AGE_RECIPIENT=$recipient
RCLONE_REMOTE=$remote
KEEP_DAYS=14
BACKUP_DIR=/var/backups/casino-dashboard
EOF
    echo "Saved $CONF. Next: $0 install"
}

install_units() {
    [ "$(id -u)" -eq 0 ] || die "run as root"
    local missing=()
    command -v age >/dev/null 2>&1 || missing+=(age)
    command -v rclone >/dev/null 2>&1 || missing+=(rclone)
    if [ "${#missing[@]}" -gt 0 ]; then
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}"
    fi
    mkdir -p "$UNIT_DIR"
    (umask 022 && cat > "$UNIT_DIR/casino-backup.service" <<EOF
[Unit]
Description=Back up the casino dashboard database (encrypted, off-host)
After=docker.service

[Service]
Type=oneshot
User=root
ExecStart=$DEPLOY/backup.sh run
Nice=10
IOSchedulingClass=idle
EOF
    )
    (umask 022 && cat > "$UNIT_DIR/casino-backup.timer" <<EOF
[Unit]
Description=Nightly casino dashboard backup

[Timer]
OnCalendar=*-*-* 03:30
RandomizedDelaySec=15min
Persistent=true

[Install]
WantedBy=timers.target
EOF
    )
    systemctl daemon-reload
    systemctl enable --now casino-backup.timer
    echo "Timer on. First backup now: systemctl start casino-backup.service"
}

run() {
    load
    local id name part size
    id="$(docker ps -q --filter "label=com.docker.compose.project=$DASH_PROJECT" \
        --filter "label=com.docker.compose.service=timescale" | head -n 1)"
    [ -n "$id" ] || fail "the timescale container of $DASH_PROJECT is not running"
    install -d -m 0700 "$BACKUP_DIR"
    name="dashboard-$(date -u +%Y%m%dT%H%M%SZ).dump.age"
    part="$BACKUP_DIR/$name.part"
    trap 'rm -f "'"$part"'"' EXIT
    if ! docker exec "$id" pg_dump -U dashboard -Fc dashboard | age -r "$AGE_RECIPIENT" > "$part"; then
        fail "pg_dump or age did not finish; nothing was kept"
    fi
    [ -s "$part" ] || fail "the encrypted dump is empty; nothing was kept"
    mv "$part" "$BACKUP_DIR/$name"
    size="$(wc -c < "$BACKUP_DIR/$name" | tr -d ' ')"
    rclone copy "$BACKUP_DIR/$name" "$RCLONE_REMOTE" \
        || fail "copy to $RCLONE_REMOTE failed; the local file $name is kept"
    find "$BACKUP_DIR" -maxdepth 1 -name "$PATTERN" -mtime +"$KEEP_DAYS" -delete
    rclone delete "$RCLONE_REMOTE" --min-age "${KEEP_DAYS}d" --include "$PATTERN" \
        || echo "backup: warning: old copies on $RCLONE_REMOTE were not removed" >&2
    echo "backup ok: $name $size bytes"
}

case "${1:-}" in
    configure) shift; configure "$@" ;;
    install) install_units ;;
    run) run ;;
    *) usage ;;
esac
