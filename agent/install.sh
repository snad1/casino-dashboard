#!/usr/bin/env bash
# Install the casino-dashboard host agent as a systemd system unit (run as root).
# Creates the casino-agent group, a random token (0600) and the env file once;
# an existing token or env file is never overwritten. An older --user copy of the unit is
# disabled and removed: it would crash-loop against the system unit over the same socket.
#   CASINO_AGENT_API_UID=10001 ./install.sh   also allow the api container's uid
set -euo pipefail

AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONF_DIR="$HOME/.config/casino-agent"
TOKEN_FILE="$CONF_DIR/token"
ENV_FILE="$CONF_DIR/agent.env"
UNIT_DIR="${CASINO_AGENT_UNIT_DIR:-/etc/systemd/system}"
USER_UNIT="$HOME/.config/systemd/user/casino-agent.service"
SOCKET=/run/casino-agent/agent.sock
BIN="$HOME/.local/bin/casino-agent"
GROUP="casino-agent"
API_UID="${CASINO_AGENT_API_UID:-}"

case "$API_UID" in
    ''|*[!0-9]*) [ -z "$API_UID" ] || { echo "CASINO_AGENT_API_UID must be a number"; exit 2; } ;;
esac
[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo -E $0): the agent is a system unit"; exit 1; }
command -v uv >/dev/null 2>&1 || { echo "uv is required: https://docs.astral.sh/uv/"; exit 1; }

if ! getent group "$GROUP" >/dev/null; then
    groupadd --system "$GROUP"
    echo "Created group $GROUP"
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
CASINO_AGENT_SOCKET=$SOCKET
# The bots this host runs, comma separated (wolfbet, goated, ...).
CASINO_AGENT_SERVICES=wolfbet
ENVEOF
    )
    echo "Created $ENV_FILE"
fi

(cd "$AGENT_DIR" && uv sync --frozen --no-dev)

# An older install put the agent in the user manager. Stop it before the system unit starts,
# or both fight over the socket and the user copy restarts every 5 s for ever.
if [ -f "$USER_UNIT" ]; then
    systemctl --user disable --now casino-agent 2>/dev/null || true
    rm -f "$USER_UNIT"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "Removed the old --user unit $USER_UNIT"
fi

install -d "$UNIT_DIR" "$(dirname "$BIN")"
sed -e "s#@AGENT_DIR@#$AGENT_DIR#g" -e "s#@CONF_DIR@#$CONF_DIR#g" -e "s#@HOME@#$HOME#g" \
    "$AGENT_DIR/systemd/casino-agent.service" > "$UNIT_DIR/casino-agent.service"
cat > "$BIN" <<BINEOF
#!/usr/bin/env bash
set -a
. "$ENV_FILE"
set +a
export CASINO_AGENT_SOCKET="\${CASINO_AGENT_SOCKET:-$SOCKET}"
# The venv does not install the project (uv package=false), so run from AGENT_DIR
# where the agent package lives; the caller's working directory must not matter.
cd "$AGENT_DIR" || exit 1
exec "$AGENT_DIR/.venv/bin/python" -m agent "\$@"
BINEOF
chmod 755 "$BIN"

systemctl daemon-reload
systemctl enable --now casino-agent
sleep 1
"$BIN" ping
