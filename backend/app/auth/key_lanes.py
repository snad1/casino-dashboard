"""Which lanes bet with which vault key right now, for roles limited to a key.

The bot's vault list says, per key, the enabled lanes whose config points at it. A key role
follows that list, so it is read on each check, cached briefly. When the bot host cannot
say, a key opens no lane: access never widens because the answer is unknown.
"""

import asyncio
import time
from dataclasses import dataclass, field

import structlog

from app.agent_client import AgentClient, AgentError

# The main key's vault id; a session recorded before the bot kept the key counts as it.
MAIN_KEY = "default"
FRESH_SECONDS = 30.0
UNKNOWN_SECONDS = 5.0

KeyMap = dict[str, frozenset[int]]

log = structlog.get_logger("app.auth.key_lanes")


def parse_key_map(data: dict[str, object]) -> KeyMap:
    """`key_list` output as {key id: lanes using it}; vault entries other than keys are left out."""
    doc = data.get("json")
    rows = doc.get("keys", []) if isinstance(doc, dict) else []
    out: KeyMap = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("kind", "key") != "key":
            continue
        lanes = row.get("used_by_lanes")
        if isinstance(row.get("id"), str) and isinstance(lanes, list):
            out[row["id"]] = frozenset(n for n in lanes if isinstance(n, int) and n >= 1)
    return out


@dataclass
class KeyLanes:
    agent: AgentClient | None = None
    _cache: dict[str, tuple[float, KeyMap]] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def use(self, agent: AgentClient | None) -> None:
        self.agent = agent
        self._cache.clear()
        self._locks.clear()

    def forget(self, service: str) -> None:
        """Drop the cached map, after a change that moves lanes between keys."""
        self._cache.pop(service, None)

    async def lanes(self, service: str) -> KeyMap:
        hit = self._cache.get(service)
        if hit is not None and hit[0] > time.monotonic():
            return hit[1]
        lock = self._locks.setdefault(service, asyncio.Lock())
        async with lock:
            hit = self._cache.get(service)
            if hit is not None and hit[0] > time.monotonic():
                return hit[1]
            found, ttl = await self._read(service)
            self._cache[service] = (time.monotonic() + ttl, found)
            return found

    async def _read(self, service: str) -> tuple[KeyMap, float]:
        if self.agent is None:
            return {}, UNKNOWN_SECONDS
        try:
            return parse_key_map(await self.agent.call(f"{service}.key_list", {})), FRESH_SECONDS
        except AgentError as error:
            log.warning("key list unavailable; key roles open no lane", code=error.code)
            return {}, UNKNOWN_SECONDS


KEY_LANES = KeyLanes()
