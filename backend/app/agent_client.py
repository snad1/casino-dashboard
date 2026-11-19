"""Talk to the host control agent over its unix socket.

One request is one JSON line, signed with the shared agent token the way the agent checks it
(`agent/agent/protocol.py`): HMAC-SHA256 over the canonical JSON of every other field, with
`ts` in epoch milliseconds so a captured request expires. Errors carry only the agent's
generic code, never the token or the raw reply.
"""

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from typing import Any

PROTO_VERSION = 1
MAX_REPLY_BYTES = 1 << 20


class AgentError(Exception):
    """The agent could not be reached, refused the request or answered with an error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _canonical(fields: dict[str, Any]) -> bytes:
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


class AgentClient:
    def __init__(self, socket_path: Path, token: str, timeout: float = 5.0) -> None:
        self.socket_path = socket_path
        self.timeout = timeout
        self._token = token.strip().encode()

    def __repr__(self) -> str:
        return f"AgentClient(socket_path={self.socket_path!s})"

    def _signed(self, cmd: str, args: dict[str, Any]) -> bytes:
        fields: dict[str, Any] = {
            "proto": PROTO_VERSION,
            "id": secrets.token_hex(16),
            "cmd": cmd,
            "args": args,
            "ts": int(time.time() * 1000),
        }
        mac = hmac.new(self._token, _canonical(fields), hashlib.sha256).hexdigest()
        return json.dumps({**fields, "mac": mac}).encode() + b"\n"

    async def call(self, cmd: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one request and return the reply's `data`; raises `AgentError` otherwise."""
        try:
            async with asyncio.timeout(self.timeout):
                reader, writer = await asyncio.open_unix_connection(
                    str(self.socket_path), limit=MAX_REPLY_BYTES
                )
                try:
                    writer.write(self._signed(cmd, args or {}))
                    await writer.drain()
                    line = await reader.readline()
                finally:
                    writer.close()
                    await writer.wait_closed()
        except TimeoutError:
            raise AgentError("timeout") from None
        except (OSError, ValueError):
            raise AgentError("unreachable") from None
        try:
            reply = json.loads(line)
        except ValueError:
            raise AgentError("bad_reply") from None
        if not isinstance(reply, dict):
            raise AgentError("bad_reply")
        if not reply.get("ok"):
            error = reply.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            raise AgentError(code if isinstance(code, str) else "bad_reply")
        data = reply.get("data")
        return data if isinstance(data, dict) else {}
