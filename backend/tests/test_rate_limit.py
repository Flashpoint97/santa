from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.config import settings
from app.db.models import RateLimitHit
from app.db.session import SessionLocal
from app.services.rate_limit import RateLimiter
from app.services.security import generate_login_code, hashes_match, keyed_hash

SECRET = settings.code_hmac_secret.get_secret_value()
WINDOW = timedelta(minutes=15)


async def test_hits_over_the_limit_are_refused_and_keys_are_independent() -> None:
    async with SessionLocal() as s:
        limiter = RateLimiter(s, secret=SECRET)
        results = [await limiter.hit("ip:a", limit=3, window=WINDOW) for _ in range(5)]
        other = await limiter.hit("ip:b", limit=3, window=WINDOW)
        await s.commit()
    assert results == [True, True, True, False, False]
    assert other is True


async def test_old_hits_fall_out_of_the_window_and_can_be_pruned() -> None:
    async with SessionLocal() as s:
        limiter = RateLimiter(s, secret=SECRET)
        for _ in range(3):
            await limiter.hit("ip:a", limit=3, window=WINDOW)
        await s.commit()
        await s.execute(
            update(RateLimitHit).values(created_at=datetime.now(UTC) - timedelta(hours=2))
        )
        await s.commit()
        assert await limiter.hit("ip:a", limit=3, window=WINDOW) is True
        await limiter.prune(older_than=timedelta(hours=1))
        await s.commit()
        assert len((await s.scalars(select(RateLimitHit))).all()) == 1


async def test_raw_key_is_never_stored() -> None:
    async with SessionLocal() as s:
        await RateLimiter(s, secret=SECRET).hit(
            "login-failure:ip:203.0.113.9", limit=1, window=WINDOW
        )
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
