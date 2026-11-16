"""Test setup.

Set TEST_DATABASE_URL to run against Postgres (CI does). Without it, a temporary SQLite file
is used so `uv run pytest` works with no setup. Env vars must be set before `app` is imported.
"""

import os
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

_tmpdir = tempfile.mkdtemp(prefix="santa-test-")
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", f"sqlite+aiosqlite:///{_tmpdir}/test.db"
)
os.environ["ENVIRONMENT"] = "test"
os.environ["JWT_SECRET"] = "test-jwt-secret-" + "x" * 32
os.environ["CODE_HMAC_SECRET"] = "test-code-secret-" + "y" * 32
os.environ["EMAIL_BACKEND"] = "console"

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.db.models import Base, Event, Participant  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.dependencies import get_email_sender  # noqa: E402
from app.main import app  # noqa: E402
from app.services.email import EmailMessage  # noqa: E402


@dataclass
class FakeEmailSender:
    sent: list[EmailMessage] = field(default_factory=list)

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)

    def last_code_for(self, email: str) -> str:
        for m in reversed(self.sent):
            if m.to == email and "code" in m.text:
                return m.subject.rsplit(" ", 1)[-1]
        raise AssertionError(f"no code sent to {email}")


@pytest.fixture(scope="session", autouse=True)
async def _schema() -> AsyncIterator[None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_tables(_schema: None) -> AsyncIterator[None]:
    yield
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(delete(table))


@pytest.fixture
def emails() -> FakeEmailSender:
    return FakeEmailSender()


@pytest.fixture
async def client(emails: FakeEmailSender) -> AsyncIterator[httpx.AsyncClient]:
    app.dependency_overrides[get_email_sender] = lambda: emails
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@dataclass
class Seeded:
    event_id: object
    admin_email: str
    emails: list[str]


async def seed_event(
    name: str = "Office", people: tuple[str, ...] = ("ana", "bruno", "cvita", "dino")
) -> Seeded:
    """First person is the admin. Emails are <name>@<event>.test."""
    domain = f"{name.lower()}.test"
    async with SessionLocal() as s:
        event = Event(name=name)
        s.add(event)
        await s.flush()
        addrs = [f"{p}@{domain}" for p in people]
        s.add_all(
            Participant(event_id=event.id, name=p.title(), email=a, is_admin=(i == 0))
            for i, (p, a) in enumerate(zip(people, addrs, strict=True))
        )
        await s.commit()
        return Seeded(event.id, addrs[0], addrs)


@pytest.fixture
async def office() -> Seeded:
    return await seed_event()


async def login(client: httpx.AsyncClient, emails: FakeEmailSender, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/request-code", json={"email": email})
    assert r.status_code == 202, r.text
    code = emails.last_code_for(email)
    r = await client.post("/api/v1/auth/verify-code", json={"email": email, "code": code})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
