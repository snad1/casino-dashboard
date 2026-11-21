#!/usr/bin/env bash
# The 7-day soak: one record a night of what the dashboard costs the bots and what it lost.
#   tools/soak.sh install     as root, once: timer at 23:50 every night
#   tools/soak.sh run         what the timer runs: append the last 24 h to the results
#   tools/soak.sh table       the days so far as a markdown table, with the verdict
#   tools/soak.sh uninstall   remove the timer after the last day
# BOT_DB (default ~/.wolfbet_autobot.db) is read-only. Results: tools/soak-results/soak.jsonl.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RESULTS="$ROOT/tools/soak-results/soak.jsonl"
DASH_ENV="$HOME/.config/casino-dashboard/dash.env"
UNIT_DIR="${SOAK_UNIT_DIR:-/etc/systemd/system}"
BOT_DB="${BOT_DB:-$HOME/.wolfbet_autobot.db}"

die() { echo "soak: $1" >&2; exit "${2:-1}"; }
usage() { sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

run() {
    [ -f "$DASH_ENV" ] || die "Dashboard not installed: run deploy/install.sh" 2
    DASH_COMPOSE_PROJECT=""
    # shellcheck disable=SC1090
    . "$DASH_ENV"
    # The compose file names its project; the Coolify app id in dash.env is not it.
    python3 "$ROOT/tools/soak_day.py" run --db "$BOT_DB" --results "$RESULTS" \
        --project "${DASH_COMPOSE_PROJECT:-wolfbet-dashboard}"
}

install_units() {
    [ "$(id -u)" -eq 0 ] || die "run as root"
    mkdir -p "$UNIT_DIR"
    (umask 022 && cat > "$UNIT_DIR/casino-soak.service" <<UNIT
[Unit]
Description=Record one casino dashboard soak day
After=docker.service

[Service]
Type=oneshot
User=root
Environment=BOT_DB=$BOT_DB
ExecStart=$ROOT/tools/soak.sh run
Nice=10
IOSchedulingClass=idle
UNIT
    )
    (umask 022 && cat > "$UNIT_DIR/casino-soak.timer" <<UNIT
[Unit]
Description=Nightly casino dashboard soak record

[Timer]
OnCalendar=*-*-* 23:50
Persistent=true

[Install]
WantedBy=timers.target
UNIT
    )
    systemctl daemon-reload
    systemctl enable --now casino-soak.timer
    echo "Timer on. Records go to $RESULTS; see them with: $0 table"
}

uninstall_units() {
    [ "$(id -u)" -eq 0 ] || die "run as root"
    systemctl disable --now casino-soak.timer || true
    rm -f "$UNIT_DIR/casino-soak.service" "$UNIT_DIR/casino-soak.timer"
    systemctl daemon-reload
    echo "Timer removed. The results stay in $RESULTS."
}

case "${1:-}" in
    install) install_units ;;
    run) run ;;
    table) python3 "$ROOT/tools/soak_day.py" table --results "$RESULTS" ;;
    uninstall) uninstall_units ;;
    *) usage ;;
esac
