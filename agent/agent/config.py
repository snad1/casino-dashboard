"""Agent settings from the environment; the token only from a private file.

Loading fails fast on anything that would leave the socket open to more than
the dashboard api: no allowlist, a missing or weak token, or a token file other
users can read. Error messages never contain the token.
"""

import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

MIN_TOKEN_BYTES = 32
MIN_DISTINCT_CHARS = 8
_PLACEHOLDER = re.compile(
    r"(change.?me|replace.?me|your[-_ ]|<)"
    r"|(secret|password|passwd|example|test|default|admin|dummy|x+)$",
    re.IGNORECASE,
)


class ConfigError(ValueError):
    """The agent must not start with this configuration."""


@dataclass(frozen=True)
class AgentConfig:
    socket_path: Path
    token: bytes
    allow_uids: frozenset[int]
    allow_gids: frozenset[int]
    group: str | None = None
    max_request_bytes: int = 64 * 1024
    idle_timeout: float = 5.0
    max_conns_per_peer: int = 4
    replay_window_ms: int = 30_000

    def __repr__(self) -> str:
        return f"AgentConfig(socket_path={self.socket_path!s}, token=<hidden>)"


def weak_token_reason(value: str) -> str | None:
    """Why `value` is not acceptable as the agent token, or None when it is fine."""
    if not value.strip():
        return "is empty"
    if _PLACEHOLDER.match(value.strip()):
        return "is a placeholder or known default"
    if len(set(value)) < MIN_DISTINCT_CHARS:
        return "has too few distinct characters"
    if len(value.encode()) < MIN_TOKEN_BYTES:
        return f"is shorter than {MIN_TOKEN_BYTES} bytes"
    return None


def _ids(name: str, raw: str) -> frozenset[int]:
    try:
        return frozenset(int(part) for part in raw.replace(",", " ").split())
    except ValueError:
        raise ConfigError(f"{name} must be a comma-separated list of numeric ids") from None


def read_token(path: Path) -> bytes:
    """The token from `path`, refused when missing, readable by group or others, or weak."""
    try:
        st = path.stat()
        text = path.read_text().strip()
    except OSError:
        raise ConfigError(f"token file {path} cannot be read") from None
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(f"token file {path} must be readable by its owner only (chmod 600)")
    if reason := weak_token_reason(text):
        raise ConfigError(f"agent token {reason}")
    return text.encode()


def load_config(env: Mapping[str, str] = os.environ) -> AgentConfig:
    """Build the agent settings from `env` (CASINO_AGENT_* and systemd's RUNTIME_DIRECTORY)."""
    socket_raw = env.get("CASINO_AGENT_SOCKET") or (
        str(Path(env["RUNTIME_DIRECTORY"]) / "agent.sock") if env.get("RUNTIME_DIRECTORY") else ""
    )
    if not socket_raw:
        raise ConfigError("set CASINO_AGENT_SOCKET (or run under systemd with RuntimeDirectory=)")
    token_file = env.get("CASINO_AGENT_TOKEN_FILE")
    if not token_file:
        raise ConfigError("set CASINO_AGENT_TOKEN_FILE")
    uids = _ids("CASINO_AGENT_ALLOW_UIDS", env.get("CASINO_AGENT_ALLOW_UIDS", ""))
    gids = _ids("CASINO_AGENT_ALLOW_GIDS", env.get("CASINO_AGENT_ALLOW_GIDS", ""))
    if not uids and not gids:
        raise ConfigError(
            "set CASINO_AGENT_ALLOW_UIDS and/or CASINO_AGENT_ALLOW_GIDS: "
            "an empty allowlist would refuse everyone"
        )
    return AgentConfig(
        socket_path=Path(socket_raw),
        token=read_token(Path(token_file)),
        allow_uids=uids,
        allow_gids=gids,
        group=env.get("CASINO_AGENT_GROUP") or None,
    )
