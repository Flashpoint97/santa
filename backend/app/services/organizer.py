"""Organizer maintenance of one event: replace a lost Code, list login status, wipe the event.

No FastAPI imports. Nothing here reads, returns or logs Assignments; logs carry participant ids
at most, never names or Codes. The plaintext Code exists only in `replace_code`'s return value.
Every function works on the event chosen by `resolve_event` (see app/services/events.py).
"""

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event, Participant, RateLimitHit, Session
from app.services.events import NoEventError, resolve_event
from app.services.security import generate_unique_code, keyed_hash

logger = structlog.get_logger(__name__)

__all__ = [
    "EventSummary",
    "NoEventError",
    "ParticipantStatus",
    "UnknownParticipantError",
    "describe_event",
    "participant_statuses",
    "replace_code",
    "wipe_event",
]


class UnknownParticipantError(Exception):
    """No Participant of the event has this display name."""


@dataclass(frozen=True)
class EventSummary:
    name: str
    participants: int


@dataclass(frozen=True)
class ParticipantStatus:
    display_name: str
    logged_in: bool


async def replace_code(
    db: AsyncSession, *, secret: str, name: str, event_name: str | None = None
) -> str:
    """Give the named Participant (case-insensitive) of the chosen event a new Code and end all
    their Sessions.

    One transaction: the new hash and the Session deletion commit together, so the old Code and
    every old token stop working at the same moment. The name is looked up only within the chosen
    event, but the new Code is unique among ALL Participants' Codes, in every event (including
    the Participant's own old one). `has_logged_in` is left as it was. Returns the plaintext
    Code, which is never stored or logged.
    """
    event = await resolve_event(db, event_name)
    wanted = name.strip().casefold()
    # Matched in Python: SQL lower() is locale-dependent for letters like č, š, ž, đ.
    members = (
        await db.scalars(
            select(Participant).where(Participant.event_id == event.id).with_for_update()
        )
    ).all()
    participant = next((p for p in members if p.display_name.casefold() == wanted), None)
    if participant is None:
        raise UnknownParticipantError

    taken = set(await db.scalars(select(Participant.code_hash)))
    code = generate_unique_code(lambda c: keyed_hash(secret, c) in taken)
    participant.code_hash = keyed_hash(secret, code)
    await db.execute(delete(Session).where(Session.participant_id == participant.id))
    await db.commit()
    logger.info("participant_code_replaced", participant_id=str(participant.id))
    return code


async def participant_statuses(
    db: AsyncSession, event_name: str | None = None
) -> list[ParticipantStatus]:
    """One entry per Participant of the chosen event, sorted by display name. Login state only."""
    event = await resolve_event(db, event_name)
    rows = (
        await db.execute(
            select(Participant.display_name, Participant.has_logged_in).where(
                Participant.event_id == event.id
            )
        )
    ).all()
    return sorted(
        (ParticipantStatus(name, bool(logged_in)) for name, logged_in in rows),
        key=lambda s: s.display_name.casefold(),
    )


async def describe_event(db: AsyncSession, event_name: str | None = None) -> EventSummary:
    """For the wipe prompt: the chosen event's name and how many people would be deleted."""
    event = await resolve_event(db, event_name)
    people = await db.scalar(
        select(func.count()).select_from(Participant).where(Participant.event_id == event.id)
    )
    return EventSummary(event.name, people or 0)


async def wipe_event(db: AsyncSession, event_name: str | None = None) -> uuid.UUID:
    """Delete the chosen event and, by ON DELETE CASCADE, its Participants, Sessions,
    Assignments and Animations; other events are untouched. Also delete every rate-limit hit
    (they belong to no event). One transaction. Raises NoEventError, UnknownEventError or
    AmbiguousEventError, deleting nothing. Returns the deleted event's id.
    """
    event = await resolve_event(db, event_name, lock=True)
    event_id = event.id
    await db.execute(delete(Event).where(Event.id == event_id))
    await db.execute(delete(RateLimitHit))
    await db.commit()
    logger.info("event_wiped", event_id=str(event_id))
    return event_id
