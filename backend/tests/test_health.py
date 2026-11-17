from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

import app.main


async def test_health_ok_with_db_up(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_health_503_with_db_down(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A real engine pointed at a database that cannot be opened (its directory is missing).
    broken = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/missing-dir/db.sqlite")
    monkeypatch.setattr(app.main, "engine", broken)
    r = await client.get("/health")
    await broken.dispose()
    assert r.status_code == 503
    assert r.json()["status"] == "degraded"


async def test_ping_answers_without_touching_the_db(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Even with the database unreachable, the keep-alive ping must answer 200.
    broken = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/missing-dir/db.sqlite")
    monkeypatch.setattr(app.main, "engine", broken)
    r = await client.get("/ping")
    await broken.dispose()
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_removed_email_and_admin_routes_stay_gone(client: httpx.AsyncClient) -> None:
    for path in ("/api/v1/auth/request-code", "/api/v1/admin/participants"):
        assert (await client.get(path)).status_code == 404
