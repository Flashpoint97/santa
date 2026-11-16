"""Login-code exchange and bearer-token resolution. No FastAPI imports.

`authenticate` is the single place a bearer token becomes a Principal. Inactivity expiry and
the activity bump (ticket 03) belong here, so every authenticated route inherits them.
"""

import secrets
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Participant, Session
from app.services.security import keyed_hash

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Principal:
    """The verified caller. Built only from a Session row, never from request input."""

    participant_id: uuid.UUID
    event_id: uuid.UUID


def hash_token(secret: str, token: str) -> str:
    # Prefix keeps the token hash space apart from the code hashes made with the same key.
    return keyed_hash(secret, f"session-token:{token}")


async def login_with_code(db: AsyncSession, *, secret: str, code: str) -> str | None:
    """Exchange a Code for a new Session token, or None if no Participant has that Code.

    Stores only the token's hash and marks the Participant as having logged in. The caller
    must not distinguish failure reasons to the client. Commits.
    """
    participant = await db.scalar(
        select(Participant).where(Participant.code_hash == keyed_hash(secret, code))
    )
    if participant is None:
        return None
    token = secrets.token_urlsafe(32)
    db.add(Session(participant_id=participant.id, token_hash=hash_token(secret, token)))
    participant.has_logged_in = True
    await db.commit()
    logger.info("login_succeeded")  # no participant identity in logs
    return token


async def authenticate(db: AsyncSession, *, secret: str, token: str) -> Principal | None:
    """Resolve a bearer token to its Principal, or None (fail closed) if unknown."""
    if not token:
        return None
    row = (
        await db.execute(
            select(Session.participant_id, Participant.event_id)
            .join(Participant, Participant.id == Session.participant_id)
            .where(Session.token_hash == hash_token(secret, token))
        )
    ).one_or_none()
    if row is None:
        return None
    return Principal(participant_id=row.participant_id, event_id=row.event_id)
