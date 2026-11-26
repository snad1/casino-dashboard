#!/usr/bin/env bash
# Prepare this host for the dashboard, which runs as a Coolify Docker Compose resource.
#
#   sudo -E deploy/install.sh --host wolfbet.example.com --mail-from no-reply@example.com \
#       [--bot-data-dir /root] [--brevo-key-file FILE] \
#       [--coolify-url https://coolify.example.com --coolify-app UUID --coolify-token-prompt] \
#       [--dry-run]
#   sudo -E deploy/install.sh --owner-email me@example.com     after the first Coolify deploy
#
# Installs what is missing (ufw, openssl, uv), the host agent, the compose secrets (random,
# made once, never printed), the firewall (SSH, 80 and 443 only) and the config `wolfctl dash`
# reads, then prints the environment to paste into the Coolify resource. Safe to run again:
# nothing that exists is replaced.
set -euo pipefail

DEPLOY="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$DEPLOY")"
SECRETS="$DEPLOY/secrets"
CONF_DIR="$HOME/.config/casino-dashboard"
AGENT_TOKEN="$HOME/.config/casino-agent/token"
UV_VERSION="0.13.8"
API_UID=10001
RANDOM_SECRETS=(session_secret totp_key postgres_password postgres_app_password
    redis_publisher_password redis_dashboard_password tg_login_secret)

HOST="" MAIL_FROM="" BOT_DATA_DIR="/root" COOLIFY_URL="" COOLIFY_APP=""
TOKEN_PROMPT=0 BREVO_FILE="" OWNER_EMAIL="" DRY=0

die() { echo "install: $1" >&2; exit "${2:-1}"; }
say() { echo "$@"; }
warn() { echo "warning: $*" >&2; }
run() {
    if [ "$DRY" -eq 1 ]; then echo "would run: $*"; else "$@"; fi
}
usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST="${2:-}"; shift 2 ;;
        --mail-from) MAIL_FROM="${2:-}"; shift 2 ;;
        --bot-data-dir) BOT_DATA_DIR="${2:-}"; shift 2 ;;
        --coolify-url) COOLIFY_URL="${2:-}"; shift 2 ;;
        --coolify-app) COOLIFY_APP="${2:-}"; shift 2 ;;
        --coolify-token-prompt) TOKEN_PROMPT=1; shift ;;
        --brevo-key-file) BREVO_FILE="${2:-}"; shift 2 ;;
        --owner-email) OWNER_EMAIL="${2:-}"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        -h|--help) usage ;;
        *) echo "install: unknown option $1" >&2; usage ;;
    esac
done

valid_host() { [[ "$1" =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$ ]]; }
valid_email() { [[ "$1" =~ ^[^@[:space:]\'\"\<\>]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$ ]]; }

# ── create the owner in the running api ──
create_owner() {
    valid_email "$OWNER_EMAIL" || die "--owner-email is not an email address" 2
    [ -f "$CONF_DIR/dash.env" ] || die "run the install first (no $CONF_DIR/dash.env)" 2
    # shellcheck disable=SC1091
    . "$CONF_DIR/dash.env"
    local id log status
    # The compose file names its project; DASH_PROJECT is the Coolify app, used only to deploy.
    id="$(docker ps -q --filter "label=com.docker.compose.project=${DASH_COMPOSE_PROJECT:-wolfbet-dashboard}" \
        --filter "label=com.docker.compose.service=api" | head -n 1)"
    [ -n "$id" ] || die "the api container is not running: deploy the resource in Coolify first"
    log="$(mktemp)"
    trap 'rm -f "$log"' EXIT
    set +e
    docker exec -it "$id" python -m app.cli create-owner --email "$OWNER_EMAIL" | tee "$log"
    status="${PIPESTATUS[0]}"
    set -e
    if [ "$status" -eq 0 ]; then
        exit 0
    fi
    if grep -q "An owner already exists." "$log"; then
        say "Nothing to do: the owner exists."
        exit 0
    fi
    exit "$status"
}

if [ -n "$OWNER_EMAIL" ]; then
    create_owner
fi

valid_host "$HOST" || die "--host must be a hostname like wolfbet.example.com" 2
valid_email "$MAIL_FROM" || die "--mail-from must be an email address" 2
[[ "$BOT_DATA_DIR" == /* ]] || die "--bot-data-dir must be an absolute path" 2
if [ -n "$COOLIFY_URL" ] && [[ ! "$COOLIFY_URL" =~ ^https?://[A-Za-z0-9.-]+(:[0-9]+)?/?$ ]]; then
    die "--coolify-url must look like https://coolify.example.com" 2
fi
if [ -n "$COOLIFY_APP" ] && [[ ! "$COOLIFY_APP" =~ ^[a-z0-9]{8,64}$ ]]; then
    die "--coolify-app must be the resource's UUID (letters and digits)" 2
fi
[ -z "$BREVO_FILE" ] || [ -s "$BREVO_FILE" ] || die "--brevo-key-file must name a non-empty file" 2
[ "$(id -u)" -eq 0 ] || die "run as root: sudo -E $0 ..."

# ── prerequisites ──
command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 \
    || die "Docker with the compose plugin is missing. Install Coolify first; it brings Docker."
missing=()
command -v ufw >/dev/null 2>&1 || missing+=(ufw)
command -v openssl >/dev/null 2>&1 || missing+=(openssl)
if [ "${#missing[@]}" -gt 0 ]; then
    say "Installing ${missing[*]}"
    run apt-get update -qq
    run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}"
fi
if ! command -v uv >/dev/null 2>&1; then
    say "Installing uv $UV_VERSION"
    run sh -c "curl -LsSf https://astral.sh/uv/$UV_VERSION/install.sh \
        | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh"
fi

# ── host agent ──
say "Host agent"
run env CASINO_AGENT_API_UID="$API_UID" bash "$ROOT/agent/install.sh"

# ── compose secrets: one value per file; the 0700 directory keeps them private ──
say "Secrets in $SECRETS"
run install -d -m 0700 "$SECRETS"
new_secret() {
    local file="$SECRETS/$1.txt"
    [ ! -s "$file" ] || return 0
    if [ "$DRY" -eq 1 ]; then say "would create $1"; return 0; fi
    (umask 022 && openssl rand -base64 36 | tr -d '\n' > "$file")
    say "created $1"
}
for name in "${RANDOM_SECRETS[@]}"; do
    new_secret "$name"
done
# Each served bot but wolfbet publishes as its own Redis user; Redis (uid 999) reads the file.
SERVICES="${DASHBOARD_SERVICES:-wolfbet}"
for service in ${SERVICES//,/ }; do
    [ "$service" = wolfbet ] && continue
    [[ "$service" =~ ^[a-z0-9-]+$ ]] || die "service names are a-z, 0-9 and -: $service"
    run install -d -m 0755 "$SECRETS/publishers"
    new_secret "publishers/$service"
done
if [ "$DRY" -eq 0 ]; then
    [ -s "$AGENT_TOKEN" ] || die "the agent token $AGENT_TOKEN is missing"
    if ! cmp -s "$AGENT_TOKEN" "$SECRETS/agent_token.txt" 2>/dev/null; then
        install -m 0644 "$AGENT_TOKEN" "$SECRETS/agent_token.txt"
        say "copied agent_token"
    fi
    if [ ! -e "$SECRETS/telegram_bot_token.txt" ]; then
        (umask 022 && : > "$SECRETS/telegram_bot_token.txt")
        say "created telegram_bot_token (empty: Telegram alerts off until you add the bot token)"
    fi
    if [ ! -e "$SECRETS/vapid_private_key.txt" ]; then
        (umask 022 && openssl ecparam -name prime256v1 -genkey -noout \
            | openssl pkcs8 -topk8 -nocrypt > "$SECRETS/vapid_private_key.txt")
        say "created vapid_private_key"
    fi
    if [ ! -s "$SECRETS/brevo_api_key.txt" ]; then
        if [ -n "$BREVO_FILE" ]; then
            install -m 0644 "$BREVO_FILE" "$SECRETS/brevo_api_key.txt"
        elif [ -t 0 ]; then
            read -rsp "Brevo API key (input hidden): " brevo_key
            echo
            [ -n "$brevo_key" ] || die "no Brevo key given"
            (umask 022 && printf '%s' "$brevo_key" > "$SECRETS/brevo_api_key.txt")
            unset brevo_key
        else
            die "no Brevo key: pass --brevo-key-file FILE" 2
        fi
        say "saved brevo_api_key"
    fi
    chmod 0644 "$SECRETS"/*.txt
fi

# ── firewall: SSH, 80 and 443 only; Redis and Postgres never public ──
say "Firewall"
ssh_port="$(sshd -T 2>/dev/null | awk '$1 == "port" {print $2; exit}' || true)"
if [ -z "$ssh_port" ]; then
    ssh_port="$(ss -Hltnp 2>/dev/null | awk '/"sshd"/ {n = split($4, a, ":"); print a[n]; exit}' || true)"
fi
[[ "$ssh_port" =~ ^[0-9]+$ ]] \
    || die "could not find the SSH port; ufw left unchanged so SSH stays reachable"
while read -r address; do
    port="${address##*:}"
    listen="${address%:*}"
    case "$port" in
        5432|6379)
            case "$listen" in
                127.0.0.1|'[::1]'|localhost) ;;
                *) die "port $port listens on $listen: Redis and Postgres must never be public" ;;
            esac
            ;;
    esac
done < <(ss -Hltn 2>/dev/null | awk '{print $4}')
run ufw default deny incoming
run ufw default allow outgoing
run ufw allow "$ssh_port/tcp"
run ufw allow 80/tcp
run ufw allow 443/tcp
run ufw allow 443/udp
run ufw --force enable
warn "Docker publishes container ports past ufw: keep Coolify's own dashboard (port 8000) behind its domain, or block it at your provider's firewall."

# ── DNS: TLS is Coolify's once the name points here ──
resolved="$(getent ahosts "$HOST" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ' || true)"
mine=" $(hostname -I 2>/dev/null || true) "
if [ -z "$resolved" ]; then
    warn "DNS: $HOST does not resolve yet; point an A record at this server before deploying."
else
    match=0
    for address in $resolved; do
        case "$mine" in *" $address "*) match=1 ;; esac
    done
    if [ "$match" -eq 1 ]; then
        say "DNS: $HOST points here."
    else
        warn "DNS: $HOST resolves to $resolved; this server has $mine(expected behind NAT)."
    fi
fi

# ── config for wolfctl dash ──
if [ "$DRY" -eq 0 ]; then
    install -d -m 0700 "$CONF_DIR"
    (umask 077 && cat > "$CONF_DIR/dash.env" <<EOF
DASH_DIR=$ROOT
DASH_PROJECT=$COOLIFY_APP
DASH_HOST=$HOST
COOLIFY_URL=${COOLIFY_URL%/}
EOF
    )
    if [ "$TOKEN_PROMPT" -eq 1 ]; then
        [ -t 0 ] || die "--coolify-token-prompt needs a terminal" 2
        read -rsp "Coolify API token (input hidden): " coolify_token
        echo
        [[ "$coolify_token" =~ ^[A-Za-z0-9|._-]+$ ]] || die "that does not look like a Coolify API token"
        (umask 077 && printf 'Authorization: Bearer %s\n' "$coolify_token" > "$CONF_DIR/coolify.header")
        unset coolify_token
        say "saved the Coolify token for wolfctl dash update"
    fi
fi

# ── what to paste into the Coolify resource ──
vapid_public=""
if [ -s "$SECRETS/vapid_private_key.txt" ]; then
    vapid_public="$(openssl ec -in "$SECRETS/vapid_private_key.txt" -pubout -outform DER 2>/dev/null \
        | tail -c 65 | base64 | tr '/+' '_-' | tr -d '=\n')"
fi
agent_gid="$(getent group casino-agent | cut -d: -f3 || true)"
host_tz="$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)"
cat <<EOF

Done. In Coolify, add a Docker Compose resource from the casino-dashboard repository
(compose file deploy/compose.yml), set the domain https://$HOST on the "caddy" service
(port 80), paste this environment, and deploy:

WOLFBET_HOST=$HOST
MAIL_FROM=$MAIL_FROM
BOT_DATA_DIR=$BOT_DATA_DIR
HOST_TZ=$host_tz
AGENT_GID=$agent_gid
AGENT_RUNTIME_DIR=/run/user/$(id -u)/casino-agent
SECRETS_DIR=$SECRETS
CADDY_ADDRESS=:80
VAPID_PUBLIC_KEY=$vapid_public
DASHBOARD_SERVICES=$SERVICES

Then create the owner: sudo -E $0 --owner-email you@example.com
EOF
