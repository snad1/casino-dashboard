#!/usr/bin/env bash
# Install the casino-dashboard host agent as a systemd --user unit for this user.
# Creates the casino-agent group, a random token (0600) and the env file once;
# an existing token or env file is never overwritten.
#   CASINO_AGENT_API_UID=10001 ./install.sh   also allow the api container's uid
set -euo pipefail

AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONF_DIR="$HOME/.config/casino-agent"
TOKEN_FILE="$CONF_DIR/token"
ENV_FILE="$CONF_DIR/agent.env"
UNIT_DIR="$HOME/.config/systemd/user"
BIN="$HOME/.local/bin/casino-agent"
GROUP="casino-agent"
API_UID="${CASINO_AGENT_API_UID:-}"

case "$API_UID" in
    ''|*[!0-9]*) [ -z "$API_UID" ] || { echo "CASINO_AGENT_API_UID must be a number"; exit 2; } ;;
esac
command -v uv >/dev/null 2>&1 || { echo "uv is required: https://docs.astral.sh/uv/"; exit 1; }

if ! getent group "$GROUP" >/dev/null; then
    if [ "$(id -u)" -eq 0 ]; then
        groupadd --system "$GROUP"
        echo "Created group $GROUP"
    else
        echo "Create the group first: sudo groupadd --system $GROUP"
        exit 1
    fi
fi
GID="$(getent group "$GROUP" | cut -d: -f3)"

install -d -m 0700 "$CONF_DIR"
if [ ! -s "$TOKEN_FILE" ]; then
    (umask 077 && python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > "$TOKEN_FILE")
    echo "Created $TOKEN_FILE"
fi
chmod 600 "$TOKEN_FILE"
if [ ! -f "$ENV_FILE" ]; then
    (umask 077 && cat > "$ENV_FILE" <<ENVEOF
CASINO_AGENT_TOKEN_FILE=$TOKEN_FILE
CASINO_AGENT_GROUP=$GROUP
CASINO_AGENT_ALLOW_UIDS=$(id -u)${API_UID:+,$API_UID}
CASINO_AGENT_ALLOW_GIDS=$GID
ENVEOF
    )
    echo "Created $ENV_FILE"
fi

(cd "$AGENT_DIR" && uv sync --frozen --no-dev)

install -d "$UNIT_DIR" "$(dirname "$BIN")"
sed "s#@AGENT_DIR@#$AGENT_DIR#g" "$AGENT_DIR/systemd/casino-agent.service" \
    > "$UNIT_DIR/casino-agent.service"
cat > "$BIN" <<BINEOF
#!/usr/bin/env bash
set -a
. "$ENV_FILE"
set +a
export CASINO_AGENT_SOCKET="\${CASINO_AGENT_SOCKET:-\${XDG_RUNTIME_DIR:-/run/user/\$(id -u)}/casino-agent/agent.sock}"
exec "$AGENT_DIR/.venv/bin/python" -m agent "\$@"
BINEOF
chmod 755 "$BIN"

systemctl --user daemon-reload
systemctl --user enable --now casino-agent
sleep 1
"$BIN" ping
