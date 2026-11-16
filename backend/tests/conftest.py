"""Test setup.

Set TEST_DATABASE_URL to run against Postgres (CI does). Without it, a temporary SQLite file
is used so `uv run pytest` works with no setup. Env vars must be set before `app` is imported.
"""

import os
import tempfile
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

_tmpdir = tempfile.mkdtemp(prefix="santa-test-")
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", f"sqlite+aiosqlite:///{_tmpdir}/test.db"
)
os.environ["ENVIRONMENT"] = "test"
os.environ["CODE_HMAC_SECRET"] = "test-code-secret-" + "y" * 32

import httpx  # noqa: E402
import pytest  # noqa: E402
import structlog  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.config import settings  # noqa: E402
from app.db.models import Base, Event, Participant  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.security import keyed_hash  # noqa: E402


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


@pytest.fixture(autouse=True)
def _restore_structlog() -> Iterator[None]:
    """The CLI points structlog at the stderr of the moment; undo that so a later test never
    logs to a stream pytest has already closed."""
    saved = structlog.get_config()
    yield
    structlog.configure(**saved)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def code_hash(code: str) -> str:
    return keyed_hash(settings.code_hmac_secret.get_secret_value(), code)


@dataclass
class Seeded:
    event_id: uuid.UUID
    participant_ids: dict[str, uuid.UUID]  # display name -> id
    codes: dict[str, str]  # display name -> plaintext code (test-only; never stored)


# Obviously synthetic: nobody real is called Person A.
SYNTHETIC_NAMES = ("Person A", "Person B", "Person C", "Person D")


async def seed_event(names: tuple[str, ...] = SYNTHETIC_NAMES) -> Seeded:
    """One event with the given participants and deterministic fake codes (111111, 222222...)."""
    codes = {n: str(i + 1) * 6 for i, n in enumerate(names)}
    async with SessionLocal() as s:
        event = Event(
            name="Test Exchange",
            budget_amount=Decimal("20.00"),
            budget_currency="EUR",
            exchange_at=datetime.now(UTC) + timedelta(days=30),
            place="Nowhere Street 1",
            after_event_message="Happy birthday to the synthetic person.",
        )
        s.add(event)
        await s.flush()
        people = {
            n: Participant(
                event_id=event.id,
                display_name=n,
                vocative_name=n,
                accusative_name=n,
                note=f"Note for {n}",
                code_hash=code_hash(codes[n]),
            )
            for n in names
        }
        s.add_all(people.values())
        await s.commit()
        return Seeded(event.id, {n: p.id for n, p in people.items()}, codes)


@pytest.fixture
async def seeded() -> Seeded:
    return await seed_event()
