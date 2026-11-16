from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.config import settings
from app.db.models import RateLimitHit
from app.db.session import SessionLocal
from app.services.rate_limit import RateLimiter
from app.services.security import generate_login_code, hashes_match, keyed_hash

SECRET = settings.code_hmac_secret.get_secret_value()
WINDOW = timedelta(minutes=15)


async def test_count_and_record_are_per_key_and_windowed() -> None:
    async with SessionLocal() as s:
        limiter = RateLimiter(s, secret=SECRET)
        for _ in range(3):
            limiter.record("ip:a")
        limiter.record("ip:b")
        await s.commit()
        assert await limiter.count("ip:a", window=WINDOW) == 3
        assert await limiter.count("ip:b", window=WINDOW) == 1
        assert await limiter.count("ip:c", window=WINDOW) == 0
        await s.execute(
            update(RateLimitHit).values(created_at=datetime.now(UTC) - timedelta(hours=2))
        )
        await s.commit()
        assert await limiter.count("ip:a", window=WINDOW) == 0


async def test_old_hits_can_be_pruned() -> None:
    async with SessionLocal() as s:
        limiter = RateLimiter(s, secret=SECRET)
        limiter.record("ip:a")
        await s.commit()
        await s.execute(
            update(RateLimitHit).values(created_at=datetime.now(UTC) - timedelta(hours=2))
        )
        limiter.record("ip:a")
        await s.commit()
        await limiter.prune(older_than=timedelta(hours=1))
        await s.commit()
        assert len((await s.scalars(select(RateLimitHit))).all()) == 1


async def test_lock_is_reentrant_for_duplicate_keys() -> None:
    # Duplicate keys must not self-deadlock (advisory xact locks are re-entrant per session,
    # and the key set is deduplicated anyway).
    async with SessionLocal() as s:
        await RateLimiter(s, secret=SECRET).lock("k", "k", "other")
        await s.rollback()


async def test_raw_key_is_never_stored() -> None:
    async with SessionLocal() as s:
        RateLimiter(s, secret=SECRET).record("login-failure:ip:203.0.113.9")
        await s.commit()
        (row,) = (await s.scalars(select(RateLimitHit))).all()
    assert "203.0.113.9" not in row.key_hash
    assert len(row.key_hash) == 64


def test_login_code_is_six_digits() -> None:
    for _ in range(200):
        assert len(code := generate_login_code()) == 6
        assert code.isdigit()


def test_keyed_hash_depends_on_the_secret() -> None:
    a, b = keyed_hash("s" * 40, "123456"), keyed_hash("t" * 40, "123456")
    assert a != b
    assert len(a) == 64
    assert hashes_match(a, keyed_hash("s" * 40, "123456"))
    assert not hashes_match(a, b)
