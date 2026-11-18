"""Sliding-window limits on sign-in attempts, per account and per client IP, in Redis."""

import hashlib
import secrets
import time

import structlog
from fastapi import HTTPException, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

ACCOUNT_LIMIT = 5
IP_LIMIT = 20
CODE_LIMIT = 5
RESET_ACCOUNT_LIMIT = 3
WINDOW_MS = 60_000
TOO_MANY = "Too many sign-in attempts. Wait a minute and try again."
TOO_MANY_CODES = "Too many codes tried. Wait a minute and try again."
TOO_MANY_RESETS = "Too many reset requests. Wait a minute and try again."
UNAVAILABLE = "Sign-in is temporarily unavailable. Try again shortly."

log = structlog.get_logger("app.auth.ratelimit")


async def _count(redis: Redis, key: str, now_ms: int) -> int:
    async with redis.pipeline(transaction=True) as pipe:
        pipe.zremrangebyscore(key, 0, now_ms - WINDOW_MS)
        pipe.zadd(key, {f"{now_ms}:{secrets.token_hex(4)}": now_ms})
        pipe.zcard(key)
        pipe.pexpire(key, WINDOW_MS)
        results = await pipe.execute()
    count: int = results[2]
    return count


async def _over(redis: Redis, limits: list[tuple[str, int]]) -> bool:
    now_ms = int(time.time() * 1000)
    try:
        counts = [await _count(redis, key, now_ms) for key, _ in limits]
    except RedisError:
        log.exception("rate limit unavailable")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE) from None
    return any(count > limit for count, (_, limit) in zip(counts, limits, strict=True))


async def check_login_rate(redis: Redis, email: str, ip: str | None) -> None:
    """Count this attempt; 429 over either limit, 503 when Redis is down (fail closed)."""
    account = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    limits = [
        (f"auth:rl:acct:{account}", ACCOUNT_LIMIT),
        (f"auth:rl:ip:{ip or 'unknown'}", IP_LIMIT),
    ]
    if await _over(redis, limits):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY)


async def check_reset_rate(redis: Redis, email: str, ip: str | None) -> None:
    """Reset requests count apart from sign-in, so asking for a link never locks anyone out."""
    account = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    limits = [
        (f"auth:rl:reset:acct:{account}", RESET_ACCOUNT_LIMIT),
        (f"auth:rl:reset:ip:{ip or 'unknown'}", IP_LIMIT),
    ]
    if await _over(redis, limits):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY_RESETS)


async def check_code_rate(redis: Redis, user_id: int) -> None:
    """Limit code checks outside sign-in, so a 6-digit code cannot be guessed by volume."""
    if await _over(redis, [(f"auth:rl:code:{user_id}", CODE_LIMIT)]):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY_CODES)
