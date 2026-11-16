from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update

from app.db.models import LoginCode
from app.db.session import SessionLocal
from tests.conftest import FakeEmailSender, Seeded, login

GENERIC = {"detail": "If this email is registered, a code has been sent."}


async def test_request_code_same_response_for_known_and_unknown(
    client: httpx.AsyncClient, emails: FakeEmailSender, office: Seeded
) -> None:
    known = await client.post("/api/v1/auth/request-code", json={"email": office.emails[1]})
    unknown = await client.post("/api/v1/auth/request-code", json={"email": "nobody@x.test"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json() == GENERIC
    assert [m.to for m in emails.sent] == [office.emails[1]]  # only the listed person got mail


async def test_email_is_case_insensitive(
    client: httpx.AsyncClient, emails: FakeEmailSender, office: Seeded
) -> None:
    r = await client.post("/api/v1/auth/request-code", json={"email": office.emails[1].upper()})
    assert r.status_code == 202
    assert emails.sent[-1].to == office.emails[1]


async def test_login_flow_returns_working_token(
    client: httpx.AsyncClient, emails: FakeEmailSender, office: Seeded
) -> None:
    headers = await login(client, emails, office.emails[1])
    r = await client.get("/api/v1/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["name"] == "Bruno"


async def test_only_hash_is_stored(
    client: httpx.AsyncClient, emails: FakeEmailSender, office: Seeded
) -> None:
    await client.post("/api/v1/auth/request-code", json={"email": office.emails[1]})
    code = emails.last_code_for(office.emails[1])
    async with SessionLocal() as s:
        row = (await s.scalars(select(LoginCode))).one()
    assert code not in row.code_hash
    assert len(row.code_hash) == 64


async def test_code_is_single_use(
    client: httpx.AsyncClient, emails: FakeEmailSender, office: Seeded
) -> None:
    email = office.emails[1]
    await client.post("/api/v1/auth/request-code", json={"email": email})
    body = {"email": email, "code": emails.last_code_for(email)}
    assert (await client.post("/api/v1/auth/verify-code", json=body)).status_code == 200
    assert (await client.post("/api/v1/auth/verify-code", json=body)).status_code == 400


async def test_new_code_supersedes_old(
    client: httpx.AsyncClient, emails: FakeEmailSender, office: Seeded
) -> None:
    email = office.emails[1]
    await client.post("/api/v1/auth/request-code", json={"email": email})
    old = emails.last_code_for(email)
    await client.post("/api/v1/auth/request-code", json={"email": email})
    new = emails.last_code_for(email)
    if old != new:
        r = await client.post("/api/v1/auth/verify-code", json={"email": email, "code": old})
        assert r.status_code == 400
    r = await client.post("/api/v1/auth/verify-code", json={"email": email, "code": new})
    assert r.status_code == 200


async def test_wrong_code_attempts_burn_the_code(
    client: httpx.AsyncClient, emails: FakeEmailSender, office: Seeded
) -> None:
    email = office.emails[1]
    await client.post("/api/v1/auth/request-code", json={"email": email})
    good = emails.last_code_for(email)
    wrong = f"{(int(good) + 1) % 1_000_000:06d}"
    for _ in range(5):
        r = await client.post("/api/v1/auth/verify-code", json={"email": email, "code": wrong})
        assert r.status_code == 400
    # Even the right code no longer works after max attempts.
    r = await client.post("/api/v1/auth/verify-code", json={"email": email, "code": good})
    assert r.status_code == 400


async def test_expired_code_rejected(
    client: httpx.AsyncClient, emails: FakeEmailSender, office: Seeded
) -> None:
    email = office.emails[1]
    await client.post("/api/v1/auth/request-code", json={"email": email})
    async with SessionLocal() as s:
        await s.execute(
            update(LoginCode).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await s.commit()
    r = await client.post(
        "/api/v1/auth/verify-code", json={"email": email, "code": emails.last_code_for(email)}
    )
    assert r.status_code == 400


async def test_unknown_email_verify_looks_like_wrong_code(
    client: httpx.AsyncClient, office: Seeded
) -> None:
    unknown = await client.post(
        "/api/v1/auth/verify-code", json={"email": "nobody@x.test", "code": "123456"}
    )
    known = await client.post(
        "/api/v1/auth/verify-code", json={"email": office.emails[1], "code": "123456"}
    )
    assert unknown.status_code == known.status_code == 400
    assert unknown.json() == known.json()


async def test_code_requests_rate_limited_per_email(
    client: httpx.AsyncClient, office: Seeded
) -> None:
    statuses = [
        (await client.post("/api/v1/auth/request-code", json={"email": "x@y.test"})).status_code
        for _ in range(6)
    ]
    assert statuses == [202] * 5 + [429]


async def test_validation_errors(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/v1/auth/request-code", json={"email": "not-an-email"})
    assert r.status_code == 422
    r = await client.post("/api/v1/auth/verify-code", json={"email": "a@b.test", "code": "12ab"})
    assert r.status_code == 422
    r = await client.post("/api/v1/auth/request-code", json={"email": "a@b.test", "extra": 1})
    assert r.status_code == 422
