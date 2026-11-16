"""Failed-login limits: per client IP and site-wide, including concurrency at the boundary."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select, update

from app.config import settings
from app.db.models import RateLimitHit, Session
from app.db.session import SessionLocal
from app.main import app
from tests.conftest import Seeded

LOGIN = "/api/v1/session"
GENERIC_400 = {"detail": "Neispravan kod."}


@asynccontextmanager
async def client_at(ip: str) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, client=(ip, 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def limits(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    def set_limits(*, per_ip: int = 3, site_wide: int = 100) -> None:
        monkeypatch.setattr(settings, "login_failures_per_ip", per_ip)
        monkeypatch.setattr(settings, "login_failures_global", site_wide)

    return set_limits


def wrong(i: int) -> dict[str, str]:
    return {"code": f"{900000 + i:06d}"}  # never a seeded code (those are 111111...)


async def hit_count() -> int:
    async with SessionLocal() as s:
        return (await s.scalar(select(func.count()).select_from(RateLimitHit))) or 0


async def test_per_ip_limit_gives_429_then_even_the_right_code_is_refused(
    seeded: Seeded, limits: Callable[..., None]
) -> None:
    limits(per_ip=3)
    async with client_at("192.0.2.1") as c:
        codes = [(await c.post(LOGIN, json=wrong(i))).status_code for i in range(5)]
        assert codes == [400, 400, 400, 429, 429]
        # Locked out: the real Code is not even checked, so lockout reveals nothing.
        r = await c.post(LOGIN, json={"code": seeded.codes["Person A"]})
        assert r.status_code == 429
    async with SessionLocal() as s:
        assert (await s.scalars(select(Session))).all() == []


async def test_per_ip_limit_is_per_ip(seeded: Seeded, limits: Callable[..., None]) -> None:
    limits(per_ip=2)
    async with client_at("192.0.2.1") as a, client_at("192.0.2.2") as b:
        for i in range(3):
            await a.post(LOGIN, json=wrong(i))
        assert (await a.post(LOGIN, json=wrong(9))).status_code == 429
        assert (await b.post(LOGIN, json=wrong(9))).status_code == 400
        assert (await b.post(LOGIN, json={"code": seeded.codes["Person A"]})).status_code == 200


async def test_site_wide_limit_applies_across_ips(
    seeded: Seeded, limits: Callable[..., None]
) -> None:
    limits(per_ip=100, site_wide=4)
    statuses = []
    for i in range(6):
        async with client_at(f"198.51.100.{i + 1}") as c:  # a fresh IP each time
            statuses.append((await c.post(LOGIN, json=wrong(i))).status_code)
    assert statuses == [400, 400, 400, 400, 429, 429]
    async with client_at("198.51.100.200") as c:  # a new IP and the right Code: still refused
        assert (await c.post(LOGIN, json={"code": seeded.codes["Person A"]})).status_code == 429


async def test_only_failures_are_recorded(seeded: Seeded, limits: Callable[..., None]) -> None:
    limits(per_ip=3)
    async with client_at("192.0.2.1") as c:
        for _ in range(10):  # successful logins never count toward the limit
            assert (await c.post(LOGIN, json={"code": seeded.codes["Person A"]})).status_code == 200
        assert await hit_count() == 0
        for i in range(5):
            await c.post(LOGIN, json=wrong(i))
        assert await hit_count() == 6  # 3 failures x (ip key + site-wide key); 429s add nothing


async def test_422_is_not_counted_as_a_failure(seeded: Seeded, limits: Callable[..., None]) -> None:
    limits(per_ip=1)
    async with client_at("192.0.2.1") as c:
        assert (await c.post(LOGIN, json={"code": "12"})).status_code == 422
        assert (await c.post(LOGIN, json={"code": seeded.codes["Person A"]})).status_code == 200


async def test_lockout_ends_when_the_failures_leave_the_window(
    seeded: Seeded, limits: Callable[..., None]
) -> None:
    limits(per_ip=2)
    async with client_at("192.0.2.1") as c:
        for i in range(2):
            await c.post(LOGIN, json=wrong(i))
        assert (await c.post(LOGIN, json=wrong(5))).status_code == 429
        async with SessionLocal() as s:
            await s.execute(
                update(RateLimitHit).values(created_at=datetime.now(UTC) - timedelta(minutes=16))
            )
            await s.commit()
        assert (await c.post(LOGIN, json={"code": seeded.codes["Person A"]})).status_code == 200


async def test_every_non_limit_failure_is_the_same_generic_400(
    seeded: Seeded, limits: Callable[..., None]
) -> None:
    limits(per_ip=100)
    async with client_at("192.0.2.1") as c:
        responses = [await c.post(LOGIN, json={"code": code}) for code in wrong_codes()]
    assert {r.status_code for r in responses} == {400}
    assert {r.text for r in responses} == {httpx.Response(400, json=GENERIC_400).text}
    assert {tuple(sorted(r.headers.items())) for r in responses} == {
        tuple(sorted(responses[0].headers.items()))
    }


def wrong_codes() -> list[str]:
    # A never-issued code, one that differs from a real code by a single digit, a repeat,
    # the all-zero code: no failure reason may show in the response.
    return ["000000", "111112", "999999", "111112", "123456"]


async def test_failures_stay_generic_after_the_limit_resets_per_ip(
    seeded: Seeded, limits: Callable[..., None]
) -> None:
    limits(per_ip=1)
    async with client_at("192.0.2.1") as a, client_at("192.0.2.2") as b:
        first = await a.post(LOGIN, json=wrong(1))
        second = await b.post(LOGIN, json=wrong(2))
    assert first.json() == second.json() == GENERIC_400


# --- concurrency at the boundary --------------------------------------------------------------


async def test_concurrent_failures_per_ip_never_exceed_the_limit(
    seeded: Seeded, limits: Callable[..., None]
) -> None:
    limits(per_ip=5)
    async with client_at("192.0.2.1") as c:
        results = await asyncio.gather(*(c.post(LOGIN, json=wrong(i)) for i in range(20)))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [400] * 5 + [429] * 15
    # Exactly 5 failures were recorded per key (ip + site-wide), never more.
    assert await hit_count() == 10


async def test_concurrent_failures_site_wide_never_exceed_the_limit(
    seeded: Seeded, limits: Callable[..., None]
) -> None:
    limits(per_ip=100, site_wide=6)

    async def attempt(i: int) -> httpx.Response:
        async with client_at(f"198.51.100.{i + 1}") as c:  # every attempt from its own IP
            return await c.post(LOGIN, json=wrong(i))

    results = await asyncio.gather(*(attempt(i) for i in range(20)))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [400] * 6 + [429] * 14
    async with SessionLocal() as s:
        # 6 failures recorded under the site-wide key (plus one per IP key).
        assert (await s.scalar(select(func.count()).select_from(RateLimitHit))) == 12


async def test_concurrent_mixed_guesses_and_real_logins_stay_consistent(
    seeded: Seeded, limits: Callable[..., None]
) -> None:
    # Right Codes in the mix must neither be counted nor deadlock the lock ordering.
    limits(per_ip=4)
    async with client_at("192.0.2.1") as c:
        results = await asyncio.gather(
            *(
                c.post(LOGIN, json=wrong(i) if i % 2 else {"code": seeded.codes["Person A"]})
                for i in range(16)
            )
        )
    wrong_statuses = [r.status_code for i, r in enumerate(results) if i % 2]
    assert wrong_statuses.count(400) <= 4
    assert await hit_count() == 2 * wrong_statuses.count(400)
