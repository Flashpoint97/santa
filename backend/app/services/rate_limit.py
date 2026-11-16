"""Sliding-window rate limiter backed by Postgres (no Redis needed at this size)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RateLimitHit
from app.services.security import keyed_hash


class RateLimiter:
    def __init__(self, session: AsyncSession, *, secret: str, window: timedelta) -> None:
        self._session = session
        self._secret = secret
        self._window = window

    async def hit(self, key: str, limit: int) -> bool:
        """Record one hit for `key`; return False if the key is over `limit` in the window.

        Rejected hits are recorded too, so hammering the endpoint keeps you locked out.
        The caller commits.
        """
        key_hash = keyed_hash(self._secret, key)
        cutoff = datetime.now(UTC) - self._window
        count = await self._session.scalar(
            select(func.count())
            .select_from(RateLimitHit)
            .where(RateLimitHit.key_hash == key_hash, RateLimitHit.created_at > cutoff)
        )
        self._session.add(RateLimitHit(key_hash=key_hash))
        return (count or 0) < limit

    async def prune(self) -> None:
        cutoff = datetime.now(UTC) - self._window
        await self._session.execute(delete(RateLimitHit).where(RateLimitHit.created_at <= cutoff))
