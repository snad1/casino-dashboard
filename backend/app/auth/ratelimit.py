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
WINDOW_MS = 60_000
TOO_MANY = "Too many sign-in attempts. Wait a minute and try again."
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


async def check_login_rate(redis: Redis, email: str, ip: str | None) -> None:
    """Count this attempt; 429 over either limit, 503 when Redis is down (fail closed)."""
    account = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    now_ms = int(time.time() * 1000)
    try:
        over_account = await _count(redis, f"auth:rl:acct:{account}", now_ms) > ACCOUNT_LIMIT
        over_ip = await _count(redis, f"auth:rl:ip:{ip or 'unknown'}", now_ms) > IP_LIMIT
    except RedisError:
        log.exception("login rate limit unavailable")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE) from None
    if over_account or over_ip:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY)
