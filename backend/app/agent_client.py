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
import re
import secrets
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROTO_VERSION = 1
MAX_REPLY_BYTES = 1 << 20
_CONFIG_KEY = re.compile(r"[a-z][a-z0-9_.]{0,47}")


class AgentError(Exception):
    """The agent could not be reached, refused the request or answered with an error."""

    def __init__(self, code: str, key: str | None = None) -> None:
        super().__init__(code)
        self.code, self.key = code, key


@dataclass(frozen=True)
class StreamEnd:
    """The final reply of a streamed command, after its last frame."""

    data: dict[str, Any]


def _canonical(fields: dict[str, Any]) -> bytes:
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _parse(line: bytes) -> dict[str, Any]:
    try:
        reply = json.loads(line)
    except ValueError:
        raise AgentError("bad_reply") from None
    if not isinstance(reply, dict):
        raise AgentError("bad_reply")
    return reply


def _data(reply: dict[str, Any]) -> dict[str, Any]:
    if not reply.get("ok"):
        error = reply.get("error")
        error = error if isinstance(error, dict) else {}
        code, key = error.get("code"), error.get("key")
        raise AgentError(
            code if isinstance(code, str) else "bad_reply",
            key if isinstance(key, str) and _CONFIG_KEY.fullmatch(key) else None,
        )
    data = reply.get("data")
    return data if isinstance(data, dict) else {}


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

    async def _open(
        self, cmd: str, args: dict[str, Any]
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        reader, writer = await asyncio.open_unix_connection(
            str(self.socket_path), limit=MAX_REPLY_BYTES
        )
        writer.write(self._signed(cmd, args))
        await writer.drain()
        return reader, writer

    @staticmethod
    async def _close(writer: asyncio.StreamWriter) -> None:
        writer.close()
        await writer.wait_closed()

    async def call(self, cmd: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one request and return the reply's `data`; raises `AgentError` otherwise."""
        try:
            async with asyncio.timeout(self.timeout):
                reader, writer = await self._open(cmd, args or {})
                try:
                    line = await reader.readline()
                finally:
                    await self._close(writer)
        except TimeoutError:
            raise AgentError("timeout") from None
        except (OSError, ValueError):
            raise AgentError("unreachable") from None
        return _data(_parse(line))

    async def stream(
        self, cmd: str, args: dict[str, Any] | None = None
    ) -> AsyncIterator[dict[str, Any] | StreamEnd]:
        """Yield each frame of a streamed command, then its final reply as `StreamEnd`.

        Only connecting is bounded by the timeout: a job may stay quiet for minutes.
        """
        try:
            async with asyncio.timeout(self.timeout):
                reader, writer = await self._open(cmd, args or {})
        except TimeoutError:
            raise AgentError("timeout") from None
        except (OSError, ValueError):
            raise AgentError("unreachable") from None
        try:
            while True:
                try:
                    line = await reader.readline()
                except (OSError, ValueError):
                    raise AgentError("unreachable") from None
                if not line:
                    raise AgentError("unreachable")
                message = _parse(line)
                if "seq" in message and "ok" not in message:
                    yield message
                    continue
                yield StreamEnd(_data(message))
                return
        finally:
            await self._close(writer)
