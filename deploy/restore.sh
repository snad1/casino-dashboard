#!/usr/bin/env bash
# Restore a dashboard backup made by deploy/backup.sh.
#
#   deploy/restore.sh drill FILE --identity KEY
#       restore into a throwaway Timescale container, print row counts and timings, remove it
#   deploy/restore.sh into FILE --identity KEY --yes-replace-dashboard-data
#       replace the running stack's data (after a fresh deploy has run its migrations)
#
# KEY is the age private key; it is only read, never copied. Timescale needs its pre- and
# post-restore steps around pg_restore. See docs/ops/restore.md.
set -euo pipefail

CONF_DIR="$HOME/.config/casino-dashboard"
IMAGE="timescale/timescaledb:2.32.0-pg16"
APP_SERVICES=(api ingest mailer)
COUNT_TABLES=(users user_roles audit_log alert_rules alert_events comments sessions bets
    reconcile_runs)

die() { echo "restore: $1" >&2; exit "${2:-1}"; }
usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

action="${1:-}"
file="${2:-}"
identity="" replace=0
[ $# -ge 2 ] && shift 2 || usage
while [ $# -gt 0 ]; do
    case "$1" in
        --identity) identity="${2:-}"; shift 2 ;;
        --yes-replace-dashboard-data) replace=1; shift ;;
        *) usage ;;
    esac
done
case "$action" in drill|into) ;; *) usage ;; esac
[ -f "$file" ] || die "no backup file at $file" 2
[ -f "$identity" ] || die "--identity must name the age key file" 2
[ "$action" = drill ] || [ "$replace" -eq 1 ] \
    || die "this replaces the dashboard's data; add --yes-replace-dashboard-data to go on" 2

sql() { docker exec -i "$1" psql -U dashboard -d dashboard -v ON_ERROR_STOP=1 -qAt -c "$2"; }

# Decrypt into pg_restore inside the container; the dump never lands on the host's disk.
load_dump() {
    local target="$1"; shift
    local log status
    log="$(mktemp)"
    set +e
    age -d -i "$identity" "$file" \
        | docker exec -i "$target" pg_restore -U dashboard -d dashboard --no-owner "$@" 2> "$log"
    status=$?
    set -e
    if [ "$status" -ne 0 ]; then
        echo "pg_restore reported $(grep -c 'error:' "$log" || true) error(s):" >&2
        tail -n 20 "$log" >&2
    fi
    rm -f "$log"
}

counts() {
    local target="$1" table
    for table in "${COUNT_TABLES[@]}"; do
        printf '  %-16s %s\n' "$table" "$(sql "$target" "SELECT count(*) FROM $table")"
    done
    printf '  %-16s %s\n' "migration" "$(sql "$target" "SELECT version_num FROM alembic_version")"
}

drill() {
    local name started ready loaded done_at
    name="casino-restore-drill-$$"
    started=$(date +%s)
    docker run -d --rm --name "$name" -e POSTGRES_USER=dashboard -e POSTGRES_DB=dashboard \
        -e POSTGRES_HOST_AUTH_METHOD=trust "$IMAGE" >/dev/null
    trap 'docker rm -f "'"$name"'" >/dev/null 2>&1 || true' EXIT
    for _ in $(seq 1 120); do
        if docker logs "$name" 2>&1 | grep -q "PostgreSQL init process complete" \
            && sql "$name" "SELECT 1" >/dev/null 2>&1; then
            break
        fi
        sleep 1
    done
    sql "$name" "SELECT 1" >/dev/null || die "the scratch database did not start"
    ready=$(date +%s)
    sql "$name" "CREATE ROLE dashboard_app NOLOGIN"
    sql "$name" "SELECT timescaledb_pre_restore()" >/dev/null
    load_dump "$name"
    sql "$name" "SELECT timescaledb_post_restore()" >/dev/null
    sql "$name" "ANALYZE"
    loaded=$(date +%s)
    echo "Restored $(basename "$file") into a scratch database:"
    counts "$name"
    done_at=$(date +%s)
    echo "Timing: start $((ready - started)) s, decrypt + restore $((loaded - ready)) s, total $((done_at - started)) s"
}

into() {
    [ -f "$CONF_DIR/dash.env" ] || die "Dashboard not installed: run deploy/install.sh" 2
    DASH_PROJECT=""
    # shellcheck disable=SC1091
    . "$CONF_DIR/dash.env"
    [ -n "$DASH_PROJECT" ] || die "no Coolify app in dash.env" 2
    local id
    id="$(docker ps -q --filter "label=com.docker.compose.project=$DASH_PROJECT" \
        --filter "label=com.docker.compose.service=timescale" | head -n 1)"
    [ -n "$id" ] || die "the timescale container of $DASH_PROJECT is not running"
    docker compose -p "$DASH_PROJECT" stop "${APP_SERVICES[@]}"
    trap 'docker compose -p "'"$DASH_PROJECT"'" start '"${APP_SERVICES[*]}"' >/dev/null' EXIT
    sql "$id" "SELECT timescaledb_pre_restore()" >/dev/null
    load_dump "$id" --clean --if-exists
    sql "$id" "SELECT timescaledb_post_restore()" >/dev/null
    sql "$id" "ANALYZE"
    echo "Restored $(basename "$file") into $DASH_PROJECT:"
    counts "$id"
}

"$action"
