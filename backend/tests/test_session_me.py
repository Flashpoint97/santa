"""Login and `GET me` through the HTTP API (in-process, real database)."""

import httpx
import pytest
from sqlalchemy import select

from app.db.models import Participant, Session
from app.db.session import SessionLocal
from app.services.sessions import hash_token
from tests.conftest import Seeded, code_hash


async def login(client: httpx.AsyncClient, code: str) -> str:
    r = await client.post("/api/v1/session", json={"code": code})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_login_returns_token_and_idle_timeout(
    client: httpx.AsyncClient, seeded: Seeded
) -> None:
    r = await client.post("/api/v1/session", json={"code": seeded.codes["Person A"]})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"token", "idle_timeout_seconds"}
    assert body["idle_timeout_seconds"] == 600
    assert len(body["token"]) >= 32


async def test_login_stores_only_token_hash_and_marks_logged_in(
    client: httpx.AsyncClient, seeded: Seeded
) -> None:
    token = await login(client, seeded.codes["Person A"])
    async with SessionLocal() as s:
        sessions = (await s.scalars(select(Session))).all()
        assert len(sessions) == 1
        row = sessions[0]
        assert row.participant_id == seeded.participant_ids["Person A"]
        assert row.token_hash != token
        assert token not in row.token_hash
        assert row.token_hash == hash_token(
            "test-code-secret-" + "y" * 32, token
        )  # keyed hash, reproducible server-side only
        assert row.created_at is not None
        assert row.last_activity_at is not None
        flags = {
            p.display_name: p.has_logged_in for p in (await s.scalars(select(Participant))).all()
        }
    assert flags["Person A"] is True
    assert flags["Person B"] is False


@pytest.mark.parametrize("bad", ["000000", "999999"])
async def test_unknown_code_is_a_generic_400(
    client: httpx.AsyncClient, seeded: Seeded, bad: str
) -> None:
    r = await client.post("/api/v1/session", json={"code": bad})
    assert r.status_code == 400
    assert set(r.json()) == {"detail"}
    assert isinstance(r.json()["detail"], str)
    async with SessionLocal() as s:
        assert (await s.scalars(select(Session))).all() == []


async def test_all_failures_share_one_message(client: httpx.AsyncClient, seeded: Seeded) -> None:
    messages = {
        (await client.post("/api/v1/session", json={"code": c})).json()["detail"]
        for c in ("000000", "123456", "654321")
    }
    assert len(messages) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"code": "12345"},
        {"code": "1234567"},
        {"code": "12345a"},
        {"code": "\uff11\uff12\uff13\uff14\uff15\uff16"},  # fullwidth digits are not digits here
        {"code": " 12345"},
        {"code": 123456},
        {},
        {"code": "123456", "extra": 1},
    ],
)
async def test_malformed_code_is_422(client: httpx.AsyncClient, seeded: Seeded, body: dict) -> None:
    r = await client.post("/api/v1/session", json=body)
    assert r.status_code == 422


async def test_me_shape_and_values(client: httpx.AsyncClient, seeded: Seeded) -> None:
    token = await login(client, seeded.codes["Person A"])
    r = await client.get("/api/v1/me", headers=bearer(token))
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "display_name",
        "vocative_name",
        "personal_note",
        "event",
        "drawn",
        "has_revealed",
    }
    assert body["display_name"] == "Person A"
    assert body["vocative_name"] == "Person A"
    assert body["personal_note"] == "Note for Person A"
    assert body["drawn"] is False
    assert body["has_revealed"] is False
    ev = body["event"]
    assert set(ev) == {
        "name",
        "budget_amount",
        "budget_currency",
        "exchange_at",
        "place",
        "after_event_message",
    }
    assert ev["name"] == "Test Exchange"
    assert ev["budget_amount"] == "20.00"
    assert ev["budget_currency"] == "EUR"
    assert ev["exchange_at"].endswith("Z")
    assert ev["place"] == "Nowhere Street 1"


async def test_empty_note_is_null(client: httpx.AsyncClient, seeded: Seeded) -> None:
    async with SessionLocal() as s:
        p = await s.get(Participant, seeded.participant_ids["Person A"])
        assert p is not None
        p.note = ""
        await s.commit()
    token = await login(client, seeded.codes["Person A"])
    r = await client.get("/api/v1/me", headers=bearer(token))
    assert r.json()["personal_note"] is None


async def test_me_never_contains_another_participants_data(
    client: httpx.AsyncClient, seeded: Seeded
) -> None:
    token_a = await login(client, seeded.codes["Person A"])
    token_b = await login(client, seeded.codes["Person B"])
    ra = await client.get("/api/v1/me", headers=bearer(token_a))
    rb = await client.get("/api/v1/me", headers=bearer(token_b))
    assert ra.json()["display_name"] == "Person A"
    assert rb.json()["display_name"] == "Person B"
    for other in ("Person B", "Person C", "Person D"):
        assert other not in ra.text
        assert f"Note for {other}" not in ra.text
    assert "Person A" not in rb.text


async def test_me_without_valid_token_is_401(client: httpx.AsyncClient, seeded: Seeded) -> None:
    await login(client, seeded.codes["Person A"])
    cases = [
        {},  # missing
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer not-a-real-token"},
        {"Authorization": "Basic abc"},
        {"Authorization": f"Bearer {seeded.codes['Person A']}"},  # a Code is not a token
    ]
    for headers in cases:
        r = await client.get("/api/v1/me", headers=headers)
        assert r.status_code == 401, headers
        assert r.headers["www-authenticate"] == "Bearer"


async def test_token_hash_is_not_usable_as_token(client: httpx.AsyncClient, seeded: Seeded) -> None:
    # A leaked DB row (the hash) must not authenticate.
    await login(client, seeded.codes["Person A"])
    async with SessionLocal() as s:
        row = (await s.scalars(select(Session))).one()
    r = await client.get("/api/v1/me", headers=bearer(row.token_hash))
    assert r.status_code == 401


async def test_each_login_gets_its_own_session(client: httpx.AsyncClient, seeded: Seeded) -> None:
    t1 = await login(client, seeded.codes["Person A"])
    t2 = await login(client, seeded.codes["Person A"])
    assert t1 != t2
    for t in (t1, t2):
        assert (await client.get("/api/v1/me", headers=bearer(t))).status_code == 200


async def test_drawn_and_has_revealed_flags(client: httpx.AsyncClient, seeded: Seeded) -> None:
    from datetime import UTC, datetime

    from app.db.models import Event

    async with SessionLocal() as s:
        ev = await s.get(Event, seeded.event_id)
        p = await s.get(Participant, seeded.participant_ids["Person A"])
        assert ev is not None
        assert p is not None
        ev.drawn_at = datetime.now(UTC)
        p.first_revealed_at = datetime.now(UTC)
        await s.commit()
    token = await login(client, seeded.codes["Person A"])
    body = (await client.get("/api/v1/me", headers=bearer(token))).json()
    assert body["drawn"] is True
    assert body["has_revealed"] is True


async def test_code_hash_helper_matches_login_lookup(seeded: Seeded) -> None:
    # Guards the test seed itself: stored hashes are keyed HMACs, never the plaintext code.
    async with SessionLocal() as s:
        hashes = set((await s.scalars(select(Participant.code_hash))).all())
    assert hashes == {code_hash(c) for c in seeded.codes.values()}
    assert not hashes & set(seeded.codes.values())


async def test_cors_allows_the_dev_frontend(client: httpx.AsyncClient) -> None:
    r = await client.options(
        "/api/v1/me",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    other = await client.options(
        "/api/v1/me",
        headers={"Origin": "http://evil.test", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in other.headers
