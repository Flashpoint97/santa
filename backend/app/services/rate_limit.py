"""Sliding-window rate limiter backed by Postgres (no Redis needed at this size).

Atomicity design: a limit check ("count hits in the window") followed by a record ("insert a
hit") is a classic check-then-act race: N concurrent requests can all read count = limit - 1
and all insert. To close it, `lock` takes a transaction-scoped Postgres advisory lock per key
(`pg_advisory_xact_lock`) *before* the count, and the caller holds the transaction open until
it has recorded and committed. A second request on the same key blocks at `lock` until the
first commits, then sees its hit. The lock is released automatically on commit/rollback, so a
crashed request can never leave a key locked. Locks are taken in sorted key order so that two
requests locking the same pair of keys cannot deadlock. SQLite (the test fallback) has no
advisory locks, but it has one writer at a time: `lock` there runs a no-op DELETE, which takes
the database write lock for the rest of the transaction (other writers wait on the busy timeout),
giving the same check-then-record serialisation, just database-wide.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RateLimitHit
from app.services.security import keyed_hash

_INT64_SPAN = 1 << 64


def _advisory_key(key_hash: str) -> int:
    """First 64 bits of the (already uniformly distributed) key hash, as a signed bigint."""
    n = int(key_hash[:16], 16)
    return n - _INT64_SPAN if n >= _INT64_SPAN // 2 else n


class RateLimiter:
    def __init__(self, session: AsyncSession, *, secret: str) -> None:
        self._session = session
        self._secret = secret

    def _hash(self, key: str) -> str:
        return keyed_hash(self._secret, key)

    async def lock(self, *keys: str) -> None:
        """Serialise every check-and-record on these keys until the transaction ends.

        Call before `count`/`record`, and commit (or roll back) only after recording.
        """
        if self._session.get_bind().dialect.name != "postgresql":
            # SQLite: a read-only transaction would let concurrent callers all count before
            # any of them records. Taking the write lock first closes that gap.
            await self._session.execute(delete(RateLimitHit).where(RateLimitHit.id < 0))
            return
        for key_hash in sorted({self._hash(k) for k in keys}):
            await self._session.execute(
                text("SELECT pg_advisory_xact_lock(:k)"), {"k": _advisory_key(key_hash)}
            )

    async def count(self, key: str, *, window: timedelta) -> int:
        """Hits recorded for `key` within `window`. Only meaningful while `lock`ed."""
        cutoff = datetime.now(UTC) - window
        total = await self._session.scalar(
            select(func.count())
            .select_from(RateLimitHit)
            .where(RateLimitHit.key_hash == self._hash(key), RateLimitHit.created_at > cutoff)
        )
        return total or 0

    def record(self, key: str) -> None:
        """Add one hit for `key`. The caller commits."""
        self._session.add(RateLimitHit(key_hash=self._hash(key)))

    async def prune(self, *, older_than: timedelta) -> None:
        """Delete hits older than the longest window in use. The caller commits."""
        cutoff = datetime.now(UTC) - older_than
        await self._session.execute(delete(RateLimitHit).where(RateLimitHit.created_at <= cutoff))
