"""Idle expiry, heartbeat and logout through the HTTP API (in-process, real database)."""

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update

from app.db.models import Session
from app.db.session import SessionLocal
from tests.conftest import Seeded
from tests.test_session_me import bearer, login


async def set_idle(minutes: float) -> None:
    """Pretend every Session was last active `minutes` ago."""
    async with SessionLocal() as s:
        await s.execute(
            update(Session).values(last_activity_at=datetime.now(UTC) - timedelta(minutes=minutes))
        )
        await s.commit()


async def last_activity() -> datetime:
    async with SessionLocal() as s:
        return (await s.scalars(select(Session))).one().last_activity_at


async def session_count() -> int:
    async with SessionLocal() as s:
        return len((await s.scalars(select(Session))).all())


async def test_idle_session_is_401_and_deleted(client: httpx.AsyncClient, seeded: Seeded) -> None:
    token = await login(client, seeded.codes["Person A"])
    await set_idle(10.5)
    r = await client.get("/api/v1/me", headers=bearer(token))
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert await session_count() == 0
    # Gone for good, not just rejected once.
    assert (await client.get("/api/v1/me", headers=bearer(token))).status_code == 401


async def test_session_within_the_timeout_is_valid_and_bumped(
    client: httpx.AsyncClient, seeded: Seeded
) -> None:
    token = await login(client, seeded.codes["Person A"])
    await set_idle(9)
    assert (await client.get("/api/v1/me", headers=bearer(token))).status_code == 200
    assert datetime.now(UTC) - await last_activity() < timedelta(minutes=1)


async def test_heartbeat_extends_the_session(client: httpx.AsyncClient, seeded: Seeded) -> None:
    token = await login(client, seeded.codes["Person A"])
    await set_idle(9)
    r = await client.post("/api/v1/session/heartbeat", headers=bearer(token))
    assert r.status_code == 204
    assert r.content == b""
    assert datetime.now(UTC) - await last_activity() < timedelta(minutes=1)
    # 9 minutes after the heartbeat is again within the timeout (and would not be without it).
    await set_idle(9)
    assert (await client.get("/api/v1/me", headers=bearer(token))).status_code == 200


async def test_heartbeat_cannot_revive_an_expired_session(
    client: httpx.AsyncClient, seeded: Seeded
) -> None:
    token = await login(client, seeded.codes["Person A"])
    await set_idle(11)
    r = await client.post("/api/v1/session/heartbeat", headers=bearer(token))
    assert r.status_code == 401
    assert await session_count() == 0


async def test_heartbeat_and_logout_without_a_valid_token_are_401(
    client: httpx.AsyncClient, seeded: Seeded
) -> None:
    await login(client, seeded.codes["Person A"])
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}):
        for method in ("POST", "DELETE"):
            url = "/api/v1/session/heartbeat" if method == "POST" else "/api/v1/session"
            r = await client.request(method, url, headers=headers)
            assert r.status_code == 401, (method, headers)
            assert r.headers["www-authenticate"] == "Bearer"
    assert await session_count() == 1  # a bad token deletes nobody else's Session


async def test_logout_ends_only_this_session_and_the_code_still_works(
    client: httpx.AsyncClient, seeded: Seeded
) -> None:
    code = seeded.codes["Person A"]
    t1 = await login(client, code)
    t2 = await login(client, code)
    other = await login(client, seeded.codes["Person B"])
    r = await client.delete("/api/v1/session", headers=bearer(t1))
    assert r.status_code == 204
    assert r.content == b""
    assert (await client.get("/api/v1/me", headers=bearer(t1))).status_code == 401
    assert (await client.get("/api/v1/me", headers=bearer(t2))).status_code == 200
    assert (await client.get("/api/v1/me", headers=bearer(other))).status_code == 200
    # Logging out a second time with the dead token is a 401, not an error.
    assert (await client.delete("/api/v1/session", headers=bearer(t1))).status_code == 401
    t3 = await login(client, code)  # same Code logs in again
    assert (await client.get("/api/v1/me", headers=bearer(t3))).status_code == 200
