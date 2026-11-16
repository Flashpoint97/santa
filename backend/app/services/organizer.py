"""Organizer maintenance: replace a lost Code, list login status, wipe everything.

No FastAPI imports. Nothing here reads, returns or logs Assignments; logs carry participant ids
at most, never names or Codes. The plaintext Code exists only in `replace_code`'s return value.
"""

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event, Participant, RateLimitHit, Session
from app.services.drawing import NoEventError
from app.services.security import generate_unique_code, keyed_hash

logger = structlog.get_logger(__name__)

__all__ = [
    "NoEventError",
    "ParticipantStatus",
    "UnknownParticipantError",
    "count_participants",
    "participant_statuses",
    "replace_code",
    "wipe_event",
]


class UnknownParticipantError(Exception):
    """No Participant of the event has this display name."""


@dataclass(frozen=True)
class ParticipantStatus:
    display_name: str
    logged_in: bool


async def replace_code(db: AsyncSession, *, secret: str, name: str) -> str:
    """Give the named Participant (case-insensitive) a new Code and end all their Sessions.

    One transaction: the new hash and the Session deletion commit together, so the old Code and
    every old token stop working at the same moment. The new Code is unique among all Codes
    (including the Participant's own old one). `has_logged_in` is left as it was. Returns the
    plaintext Code, which is never stored or logged.
    """
    wanted = name.strip().casefold()
    # Matched in Python: SQL lower() is locale-dependent for letters like č, š, ž, đ.
    people = (await db.scalars(select(Participant).with_for_update())).all()
    if not people:
        raise NoEventError
    participant = next((p for p in people if p.display_name.casefold() == wanted), None)
    if participant is None:
        raise UnknownParticipantError

    taken = {p.code_hash for p in people}
    code = generate_unique_code(lambda c: keyed_hash(secret, c) in taken)
    participant.code_hash = keyed_hash(secret, code)
    await db.execute(delete(Session).where(Session.participant_id == participant.id))
    await db.commit()
    logger.info("participant_code_replaced", participant_id=str(participant.id))
    return code


async def participant_statuses(db: AsyncSession) -> list[ParticipantStatus]:
    """One entry per Participant, sorted by display name. Login state only, nothing else."""
    if not await db.scalar(select(func.count()).select_from(Event)):
        raise NoEventError
    rows = (await db.execute(select(Participant.display_name, Participant.has_logged_in))).all()
    return sorted(
        (ParticipantStatus(name, bool(logged_in)) for name, logged_in in rows),
        key=lambda s: s.display_name.casefold(),
    )


async def count_participants(db: AsyncSession) -> int:
    """For the wipe prompt: how many people would be deleted. Raises NoEventError if none."""
    if not await db.scalar(select(func.count()).select_from(Event)):
        raise NoEventError
    return await db.scalar(select(func.count()).select_from(Participant)) or 0


async def wipe_event(db: AsyncSession) -> uuid.UUID:
    """Delete the event and, by ON DELETE CASCADE, all Participants, Sessions, Assignments and
    Animations; also delete every rate-limit hit (they belong to no event). One transaction.
    Raises NoEventError, deleting nothing, if there is no event. Returns the deleted event's id.
    """
    event_id = await db.scalar(select(Event.id).with_for_update())
    if event_id is None:
        raise NoEventError
    await db.execute(delete(Event))
    await db.execute(delete(RateLimitHit))
    await db.commit()
    logger.info("event_wiped", event_id=str(event_id))
    return event_id
