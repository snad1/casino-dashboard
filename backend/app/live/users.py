"""The live model of a bot that runs one engine per Telegram user: one aggregator per user.

Every user's events share the bot's pid and lane 1, and session ids restart per user, so each
user gets an `Aggregator` of their own and a tick reports what changed per user. Which users
exist comes from history: a user is known once ingest has copied their database.
"""

from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import Event
from app.history.models import DbEpoch
from app.live.aggregator import Aggregator, Tick
from app.live.consumer import Gap

MAX_USERS = 1000

log = structlog.get_logger("app.live.users")


@dataclass(frozen=True)
class UserTicks:
    by_user: dict[int, Tick]

    @property
    def empty(self) -> bool:
        return not self.by_user


class UserAggregators:
    def __init__(self, svc: str) -> None:
        self.svc = svc
        self.users: dict[int, Aggregator] = {}

    async def accept(self, item: Event | Gap) -> None:
        """Sink for the stream consumer; an item without a user is not this bot's and is
        dropped."""
        uid = item.scope
        if uid is None:
            return
        aggregator = self.users.get(uid)
        if aggregator is None:
            aggregator = self.users[uid] = Aggregator(self.svc)
        aggregator.apply(item)

    def tick(self) -> UserTicks:
        ticks = {uid: agg.tick() for uid, agg in self.users.items()}
        return UserTicks({uid: tick for uid, tick in ticks.items() if not tick.empty})


async def known_uids(sessionmaker: async_sessionmaker[AsyncSession], service: str) -> list[int]:
    """The users whose databases ingest has seen, at most `MAX_USERS` (lowest ids first)."""
    async with sessionmaker() as db:
        rows = await db.scalars(
            select(DbEpoch.tg_uid)
            .where(DbEpoch.service == service, DbEpoch.tg_uid.is_not(None))
            .distinct()
            .order_by(DbEpoch.tg_uid)
            .limit(MAX_USERS + 1)
        )
        uids = [uid for uid in rows if uid is not None]
    if len(uids) > MAX_USERS:
        log.warning("too many users to follow live; the rest wait", svc=service, cap=MAX_USERS)
    return uids[:MAX_USERS]
