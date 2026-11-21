"""The Telegram bot's per-user commands, passed on to its control socket.

The TG bot keeps every Telegram user's sessions in its own process. It listens on a unix
socket that speaks this agent's protocol, signed with the bot's own control token (the
bot creates it; the agent runs as the same user and reads it). The agent is the socket's
only client, and the api never reaches it. Arguments are typed and closed here, so a bad
request never leaves the agent; the bot checks them again and answers with the same
generic codes.
"""

import asyncio
import json
import os
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, StringConstraints

from agent.config import ConfigError, read_token
from agent.errors import Busy, NotFound
from agent.protocol import signed_request
from agent.runner import CommandTimeout
from agent.validators import Args, InvalidArgs, parse_args

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
Uid = Annotated[int, Field(ge=1, le=2**53)]
Slot = Annotated[int, Field(ge=1, le=1000)]
REPLY_LIMIT = 1024 * 1024
TIMEOUT_S = 10.0
START_TIMEOUT_S = 30.0


class UserArgs(Args):
    uid: Uid


class SlotArgs(Args):
    uid: Uid
    slot: Slot | None = None


class SetArgs(Args):
    uid: Uid
    key: Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,32}$")]
    value: Annotated[str, StringConstraints(min_length=1, max_length=256)]


COMMANDS: dict[str, type[Args]] = {
    "start": UserArgs,
    "stop": SlotArgs,
    "pause": SlotArgs,
    "resume": SlotArgs,
    "set": SetArgs,
    "status": UserArgs,
    "config": UserArgs,
}


@dataclass(frozen=True)
class WolfBetTgPaths:
    """Where the TG bot's control socket and token live (the bot's data dir by default)."""

    socket: Path
    token_file: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "WolfBetTgPaths":
        home = Path(env.get("HOME") or Path.home())
        data = Path(env.get("CASINO_AGENT_WOLFBET_TG_DATA") or home / ".wolfbot_tg")
        paths = cls(
            socket=Path(env.get("CASINO_AGENT_WOLFBET_TG_SOCKET") or data / "control.sock"),
            token_file=Path(
                env.get("CASINO_AGENT_WOLFBET_TG_TOKEN_FILE") or data / "control.token"
            ),
        )
        if not (paths.socket.is_absolute() and paths.token_file.is_absolute()):
            raise ValueError("the TG control socket and token paths must be absolute")
        return paths


class WolfBetTgService:
    """Send one validated, signed command to the TG bot and map its answer to agent errors."""

    def __init__(self, paths: WolfBetTgPaths, clock: Callable[[], float] = time.time) -> None:
        self.paths = paths
        self.clock = clock

    async def call(self, cmd: str, raw: dict[str, Any]) -> dict[str, Any]:
        args = parse_args(COMMANDS[cmd], raw)
        try:
            token = read_token(self.paths.token_file)
        except ConfigError as e:
            raise Busy(f"the TG bot's control token is not usable: {e}") from None
        request = signed_request(
            token,
            secrets.token_hex(16),
            cmd,
            int(self.clock() * 1000),
            args.model_dump(exclude_none=True),
        )
        timeout = START_TIMEOUT_S if cmd == "start" else TIMEOUT_S
        try:
            reply = await asyncio.wait_for(self._exchange(request), timeout)
        except TimeoutError:
            raise CommandTimeout(f"the TG bot did not answer {cmd} in {timeout:.0f}s") from None
        except (OSError, ValueError) as e:
            raise Busy(
                f"the TG bot's control socket is not reachable ({type(e).__name__})"
            ) from None
        data = reply.get("data")
        if reply.get("ok") is True and isinstance(data, dict):
            return data
        code = (reply.get("error") or {}).get("code")
        if code == "bad_request":
            raise InvalidArgs(f"the TG bot refused {cmd}")
        if code == "not_found":
            raise NotFound(f"no matching TG session for {cmd}")
        if code == "busy":
            raise Busy(f"the TG bot is busy ({cmd})")
        raise RuntimeError(f"the TG bot answered {cmd} with {code or 'no code'}")

    async def _exchange(self, request: dict[str, Any]) -> dict[str, Any]:
        reader, writer = await asyncio.open_unix_connection(
            str(self.paths.socket), limit=REPLY_LIMIT
        )
        try:
            writer.write(json.dumps(request).encode() + b"\n")
            await writer.drain()
            line = await reader.readline()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
        if not line:
            raise ConnectionError("the TG bot closed the connection without a reply")
        reply: dict[str, Any] = json.loads(line)
        return reply

    def handlers(self) -> dict[str, Handler]:
        """Agent commands `wolfbet_tg.<name>` for every TG control command."""

        def bound(name: str) -> Handler:
            return lambda raw: self.call(name, raw)

        return {f"wolfbet_tg.{name}": bound(name) for name in COMMANDS}
