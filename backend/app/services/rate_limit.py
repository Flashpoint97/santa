"""Sliding-window rate limiter backed by Postgres (no Redis needed at this size)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RateLimitHit
from app.services.security import keyed_hash


class RateLimiter:
    def __init__(self, session: AsyncSession, *, secret: str) -> None:
        self._session = session
        self._secret = secret

    async def hit(self, key: str, *, limit: int, window: timedelta) -> bool:
        """Record one hit for `key`; return False if the key is over `limit` within `window`.

        The window is per call because the per-IP and site-wide limits use different ones.
        Rejected hits are recorded too, so hammering the endpoint keeps you locked out.
        The caller commits.
        """
        key_hash = keyed_hash(self._secret, key)
        cutoff = datetime.now(UTC) - window
        count = await self._session.scalar(
            select(func.count())
            .select_from(RateLimitHit)
            .where(RateLimitHit.key_hash == key_hash, RateLimitHit.created_at > cutoff)
        )
        self._session.add(RateLimitHit(key_hash=key_hash))
        return (count or 0) < limit

    async def prune(self, *, older_than: timedelta) -> None:
        """Delete hits older than the longest window in use. The caller commits."""
        cutoff = datetime.now(UTC) - older_than
        await self._session.execute(delete(RateLimitHit).where(RateLimitHit.created_at <= cutoff))
