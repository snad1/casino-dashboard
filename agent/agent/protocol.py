"""Wire format between the api and the agent (JSON lines over a unix socket).

A request is signed: `mac` is HMAC-SHA256 with the shared agent token over the
canonical JSON of every other request field (sorted keys, no spaces, ASCII), and
`ts` (epoch milliseconds) bounds how long a captured request stays usable.
"""

import hashlib
import hmac
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PROTO_VERSION = 1

ErrorCode = Literal[
    "bad_request",
    "unauthorized",
    "replayed",
    "too_large",
    "unknown_command",
    "busy",
    "timeout",
    "not_found",
    "internal",
]


class Request(BaseModel):
    """One request from the api; unknown fields and loose types are refused."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    proto: Literal[1]
    id: str = Field(min_length=1, max_length=64)
    cmd: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.]*$")
    args: dict[str, Any] = Field(default_factory=dict)
    ts: int = Field(ge=0)
    mac: str = Field(pattern=r"^[0-9a-f]{64}$")


class Error(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ErrorCode


class Response(BaseModel):
    """The agent's answer: data when ok, otherwise a generic error code and nothing else."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str | None
    ok: bool
    data: dict[str, Any] | None = None
    error: Error | None = None

    def line(self) -> bytes:
        """One JSON line: id always (null when the request could not be read), data or error."""
        drop = {k for k in ("data", "error") if getattr(self, k) is None}
        return self.model_dump_json(exclude=drop).encode() + b"\n"


class Frame(BaseModel):
    """One output line of a job, sent before the final reply to job.stream."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    seq: int
    stream: Literal["stdout", "stderr"]
    line: str

    def wire(self) -> bytes:
        return self.model_dump_json().encode() + b"\n"


@dataclass(frozen=True)
class Streaming:
    """A handler result sent as frames (seq, stream, line) and then one final reply body."""

    frames: AsyncIterator[dict[str, Any]]
    final: Callable[[], Awaitable[dict[str, Any]]]


def canonical_body(fields: dict[str, Any]) -> bytes:
    """The bytes a request mac covers: every field except `mac`, in one fixed encoding."""
    body = {k: v for k, v in fields.items() if k != "mac"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sign(token: bytes, fields: dict[str, Any]) -> str:
    return hmac.new(token, canonical_body(fields), hashlib.sha256).hexdigest()


def verify_mac(token: bytes, request: Request) -> bool:
    expected = sign(token, request.model_dump())
    return hmac.compare_digest(expected, request.mac)


def signed_request(
    token: bytes, req_id: str, cmd: str, ts_ms: int, args: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build a request dict with its mac, as a client sends it."""
    fields: dict[str, Any] = {
        "proto": PROTO_VERSION,
        "id": req_id,
        "cmd": cmd,
        "args": args or {},
        "ts": ts_ms,
    }
    return {**fields, "mac": sign(token, fields)}
