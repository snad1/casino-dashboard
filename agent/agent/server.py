"""The agent's unix-socket server: who may connect, what a request must prove, what it may run.

Every connection is checked against the uid/gid allowlist with the kernel's peer
credentials before a byte is read. Every request line must be at most
`max_request_bytes`, parse as a closed `Request`, carry a valid mac, be inside the
replay window and use an id not seen before. Failures get one generic error code;
the reason is logged here only. Commands come from a fixed registry, never from argv.
"""

import asyncio
import grp
import json
import logging
import os
import stat
import time
from collections import OrderedDict, defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from agent import __version__
from agent.config import AgentConfig
from agent.errors import Busy, NotFound
from agent.peercred import Peer, current_peer, peer_credentials
from agent.protocol import (
    PROTO_VERSION,
    Error,
    ErrorCode,
    Frame,
    Request,
    Response,
    Streaming,
    verify_mac,
)
from agent.runner import CommandTimeout
from agent.validators import InvalidArgs

log = logging.getLogger("casino-agent")

Handler = Callable[[dict[str, Any]], Awaitable[Any]]
REPLAY_CACHE_MAX = 100_000
_CLOSE_AFTER: frozenset[ErrorCode] = frozenset(
    {"bad_request", "unauthorized", "replayed", "too_large"}
)


async def _ping(_args: dict[str, Any]) -> dict[str, Any]:
    return {"pong": True}


async def _version(_args: dict[str, Any]) -> dict[str, Any]:
    return {"agent": __version__, "proto": PROTO_VERSION}


DEFAULT_HANDLERS: dict[str, Handler] = {"ping": _ping, "version": _version}


def _error(req_id: str | None, code: ErrorCode) -> Response:
    return Response(id=req_id, ok=False, error=Error(code=code))


class _Refused(Exception):
    def __init__(self, code: ErrorCode, req_id: str | None, reason: str) -> None:
        super().__init__(reason)
        self.code, self.req_id, self.reason = code, req_id, reason


class AgentServer:
    """Serve `handlers` on `config.socket_path`; `clock` returns epoch seconds (tests pin it)."""

    def __init__(
        self,
        config: AgentConfig,
        handlers: dict[str, Handler] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.handlers = dict(DEFAULT_HANDLERS if handlers is None else handlers)
        self.clock = clock
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._conns: defaultdict[int, int] = defaultdict(int)
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        path = self.config.socket_path
        if path.is_symlink() or (path.exists() and not stat.S_ISSOCK(path.stat().st_mode)):
            raise RuntimeError(f"{path} exists and is not a socket; refusing to replace it")
        path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle, path=str(path), limit=self.config.max_request_bytes + 1
        )
        os.chmod(path, 0o660)  # nosec B103: the casino-agent group is who may connect
        if self.config.group:
            gid = grp.getgrnam(self.config.group).gr_gid
            os.chown(path, -1, gid)
            os.chown(path.parent, -1, gid)
        log.info("listening on %s", path)

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        if self._server is None:
            raise RuntimeError("server did not start")
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self.config.socket_path.unlink(missing_ok=True)

    def _peer_allowed(self, uid: int, gid: int) -> bool:
        return uid in self.config.allow_uids or gid in self.config.allow_gids

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            uid, gid = peer_credentials(writer.get_extra_info("socket"))
        except OSError as e:
            log.warning("refused a connection: peer credentials unavailable (%s)", e)
            await self._reply(writer, _error(None, "unauthorized"))
            await self._close(writer)
            return
        if not self._peer_allowed(uid, gid):
            log.warning("refused peer uid=%d gid=%d: not in the allowlist", uid, gid)
            await self._reply(writer, _error(None, "unauthorized"))
            await self._close(writer)
            return
        if self._conns[uid] >= self.config.max_conns_per_peer:
            log.warning("refused peer uid=%d: %d connections open", uid, self._conns[uid])
            await self._reply(writer, _error(None, "busy"))
            await self._close(writer)
            return
        self._conns[uid] += 1
        try:
            await self._serve_connection(reader, writer, Peer(uid, gid))
        finally:
            self._conns[uid] -= 1
            await self._close(writer)

    async def _serve_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, peer: Peer
    ) -> None:
        uid = peer.uid
        while True:
            try:
                line = await asyncio.wait_for(reader.readline(), self.config.idle_timeout)
            except TimeoutError:
                log.info("closed idle connection from uid=%d", uid)
                return
            except (ValueError, asyncio.LimitOverrunError):
                log.warning(
                    "refused request from uid=%d: over %d bytes", uid, self.config.max_request_bytes
                )
                await self._reply(writer, _error(None, "too_large"))
                return
            if not line:
                return
            response = await self._process(line, peer)
            if isinstance(response, tuple):
                req_id, streaming = response
                if not await self._stream(writer, req_id, streaming):
                    return
                continue
            await self._reply(writer, response)
            if response.error is not None and response.error.code in _CLOSE_AFTER:
                return

    async def _stream(self, writer: asyncio.StreamWriter, req_id: str, s: Streaming) -> bool:
        """Write each frame, then the final reply; False when the client went away."""
        try:
            async for item in s.frames:
                writer.write(Frame(id=req_id, **item).wire())
                await writer.drain()
            writer.write(Response(id=req_id, ok=True, data=await s.final()).line())
            await writer.drain()
        except (ConnectionError, OSError) as e:
            log.info("stream %s ended: client gone (%s)", req_id, type(e).__name__)
            return False
        return True

    async def _process(self, line: bytes, peer: Peer) -> Response | tuple[str, Streaming]:
        uid = peer.uid
        try:
            request = self._authenticate(line)
        except _Refused as e:
            log.warning("refused request id=%s from uid=%d: %s", e.req_id, uid, e.reason)
            return _error(e.req_id, e.code)
        handler = self.handlers.get(request.cmd)
        if handler is None:
            log.warning("unknown command %r (id=%s, uid=%d)", request.cmd, request.id, uid)
            return _error(request.id, "unknown_command")
        token = current_peer.set(peer)
        try:
            data = await handler(dict(request.args))
        except InvalidArgs as e:
            log.warning("refused %s (id=%s, uid=%d): %s", request.cmd, request.id, uid, e)
            return _error(request.id, "bad_request")
        except CommandTimeout as e:
            log.warning("command %s timed out (id=%s): %s", request.cmd, request.id, e)
            return _error(request.id, "timeout")
        except Busy as e:
            log.warning("refused %s (id=%s, uid=%d): %s", request.cmd, request.id, uid, e)
            return _error(request.id, "busy")
        except NotFound:
            log.info("%s: no such job (id=%s)", request.cmd, request.id)
            return _error(request.id, "not_found")
        except Exception:
            log.exception("command %s failed (id=%s)", request.cmd, request.id)
            return _error(request.id, "internal")
        finally:
            current_peer.reset(token)
        log.info("ran %s (id=%s, uid=%d)", request.cmd, request.id, uid)
        if isinstance(data, Streaming):
            return request.id, data
        return Response(id=request.id, ok=True, data=data)

    def _authenticate(self, line: bytes) -> Request:
        if len(line) > self.config.max_request_bytes:
            raise _Refused("too_large", None, "request over the size cap")
        try:
            raw = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise _Refused("bad_request", None, "not JSON") from None
        req_id = raw.get("id") if isinstance(raw, dict) and isinstance(raw.get("id"), str) else None
        req_id = req_id[:64] if req_id else None
        try:
            request = Request.model_validate(raw)
        except ValidationError as e:
            raise _Refused("bad_request", req_id, f"{e.error_count()} field error(s)") from None
        if not verify_mac(self.config.token, request):
            raise _Refused("unauthorized", req_id, "bad mac")
        now_ms = int(self.clock() * 1000)
        if abs(now_ms - request.ts) > self.config.replay_window_ms:
            raise _Refused("unauthorized", req_id, f"ts {now_ms - request.ts} ms off")
        self._remember(request.id, now_ms)
        return request

    def _remember(self, req_id: str, now_ms: int) -> None:
        while self._seen and next(iter(self._seen.values())) <= now_ms:
            self._seen.popitem(last=False)
        if req_id in self._seen:
            raise _Refused("replayed", req_id, "id already used")
        if len(self._seen) >= REPLAY_CACHE_MAX:
            raise _Refused("busy", req_id, "replay cache full")
        self._seen[req_id] = now_ms + 2 * self.config.replay_window_ms

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, response: Response) -> None:
        try:
            writer.write(response.line())
            await writer.drain()
        except (ConnectionError, OSError) as e:
            log.info("could not send a reply: %s", e)

    @staticmethod
    async def _close(writer: asyncio.StreamWriter) -> None:
        if writer.is_closing():
            return
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError) as e:
            log.debug("connection closed with an error: %s", e)
