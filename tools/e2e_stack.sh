#!/usr/bin/env bash
# Throwaway end-to-end stack: build, start, seed, and tear down with its volumes.
#   tools/e2e_stack.sh up               generate secrets, build, start, migrate and seed
#   tools/e2e_stack.sh down             remove containers, volumes and generated secrets
#   tools/e2e_stack.sh compose <args>   docker compose against the e2e project
# The seeded viewer signs in with E2E_EMAIL and the generated e2e_password secret.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY="$ROOT/deploy"
SECRETS="$DEPLOY/e2e-secrets"
NAMES=(session_secret totp_key postgres_password postgres_app_password
    redis_publisher_password redis_dashboard_password agent_token brevo_api_key e2e_password)
export E2E_EMAIL="${E2E_EMAIL:-viewer@e2e.test}"

compose() {
    docker compose --project-directory "$DEPLOY" --env-file "$DEPLOY/e2e.env" \
        -f "$DEPLOY/compose.yml" -f "$DEPLOY/compose.e2e.yml" "$@"
}

make_secrets() {
    mkdir -p "$SECRETS" "$DEPLOY/e2e-agent" "$DEPLOY/e2e-botdata"
    chmod 700 "$SECRETS"
    for name in "${NAMES[@]}"; do
        [ -s "$SECRETS/$name.txt" ] || openssl rand -hex 24 > "$SECRETS/$name.txt"
        chmod 644 "$SECRETS/$name.txt"
    done
    : > "$SECRETS/telegram_bot_token.txt"
    : > "$SECRETS/vapid_private_key.txt"
    chmod 644 "$SECRETS/telegram_bot_token.txt" "$SECRETS/vapid_private_key.txt"
    E2E_PASSWORD="$(cat "$SECRETS/e2e_password.txt")"
    export E2E_PASSWORD
}

up() {
    docker info >/dev/null 2>&1 || { echo "the e2e stack needs Docker running" >&2; exit 1; }
    make_secrets
    (cd "$ROOT/frontend" && pnpm build >/dev/null)
    compose build api
    compose up -d --wait timescale redis
    compose run --rm migrate
    compose run --rm -e E2E_EMAIL -e E2E_PASSWORD -v "$ROOT/tools:/tools:ro" \
        --entrypoint python api /tools/e2e_seed.py
    compose up -d --wait api caddy replay
    echo "e2e stack on https://localhost:8443 (viewer $E2E_EMAIL)"
}

down() {
    compose --profile tools down -v --remove-orphans || true
    for name in "${NAMES[@]}"; do
        rm -f "$SECRETS/$name.txt"
    done
    rmdir "$SECRETS" "$DEPLOY/e2e-agent" "$DEPLOY/e2e-botdata" 2>/dev/null || true
}

case "${1:-}" in
    up) up ;;
    down) down ;;
    compose) shift; compose "$@" ;;
    *) echo "usage: $0 up|down|compose <args>" >&2; exit 2 ;;
esac
