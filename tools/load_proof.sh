#!/usr/bin/env bash
# The dashboard load proof, run on the bot's host while the bots bet as usual.
#   tools/load_proof.sh sample off [MINUTES]   bot gaps and WAL with no load stack (default 60)
#   tools/load_proof.sh up [MINUTES]           start the load stack: its own Redis and
#                                              Postgres, replay at 10x, ingest backfilling the
#                                              bot database read-only, 100 live viewers
#   tools/load_proof.sh sample on [MINUTES]    the same numbers while the load stack runs
#   tools/load_proof.sh report                 compare off and on; exit 1 if the gate fails
#   tools/load_proof.sh down                   remove the load stack and its data
# BOT_DB (default ~/.wolfbet_autobot.db) is the database measured; its directory is what
# ingest reads. Results go to tools/load-results/. Live data and alerts are never touched.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RESULTS="$ROOT/tools/load-results"
BOT_DB="${BOT_DB:-$HOME/.wolfbet_autobot.db}"
CLIENTS=100
VIEWERS=20

export COMPOSE_PROJECT_NAME=wolfbet-loadproof
export E2E_OVERLAY="$ROOT/deploy/compose.loadproof.yml"
export E2E_VIEWERS="$VIEWERS"
export BOT_DATA_DIR
BOT_DATA_DIR="$(dirname "$BOT_DB")"
export HOST_TZ
HOST_TZ="$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)"

stack() { "$ROOT/tools/e2e_stack.sh" "$@"; }

minutes() {
    case "${1:-60}" in
        ''|*[!0-9]*) echo "minutes must be a whole number" >&2; exit 2 ;;
        *) echo "${1:-60}" ;;
    esac
}

up() {
    local run_minutes
    run_minutes="$(minutes "${1:-130}")"
    [ -f "$BOT_DB" ] || { echo "no bot database at $BOT_DB (set BOT_DB)" >&2; exit 2; }
    mkdir -p "$RESULTS"
    chmod 700 "$RESULTS"
    stack up
    stack compose up -d ingest
    local password
    password="$(cat "$ROOT/deploy/e2e-secrets/e2e_password.txt")"
    (umask 077 && for n in $(seq 1 "$VIEWERS"); do
        echo "viewer$n@e2e.test $password"
    done > "$RESULTS/accounts.txt")
    (cd "$ROOT/backend" && nohup uv run python -m tests.load.ws_fanout \
        --api http://127.0.0.1:8010 --origin https://localhost:8443 \
        --accounts "$RESULTS/accounts.txt" --clients "$CLIENTS" --bet-rate 0 \
        --seconds "$((run_minutes * 60))" > "$RESULTS/viewers.json" 2> "$RESULTS/viewers.log" &)
    echo "Load stack up: replay 10x, ingest backfilling $BOT_DATA_DIR, $CLIENTS viewers for $run_minutes min."
    echo "Now: tools/load_proof.sh sample on 60"
}

down() {
    pkill -f "tests.load.ws_fanout" 2>/dev/null || true
    stack down
    rm -f "$RESULTS/accounts.txt"
}

case "${1:-}" in
    sample)
        label="${2:-}"
        case "$label" in off|on) ;; *) echo "usage: $0 sample off|on [MINUTES]" >&2; exit 2 ;; esac
        python3 "$ROOT/tools/load_proof.py" sample --db "$BOT_DB" --label "$label" \
            --minutes "$(minutes "${3:-60}")" --out "$RESULTS"
        ;;
    up) up "${2:-}" ;;
    report) python3 "$ROOT/tools/load_proof.py" report "$RESULTS/off.json" "$RESULTS/on.json" ;;
    down) down ;;
    *) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2 ;;
esac
