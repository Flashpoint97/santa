"""Create an event, its participants and Codes from a validated list. No FastAPI imports."""

from collections.abc import Mapping
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Animation, Event, Participant
from app.services.animation import VIDEO_CONTENT_TYPE
from app.services.list_file import ListFile
from app.services.security import generate_unique_code, keyed_hash

logger = structlog.get_logger(__name__)


class EventExistsError(Exception):
    """An event with the same name (casefold) is already loaded."""


@dataclass(frozen=True)
class IssuedCode:
    display_name: str
    code: str  # plaintext, exists only in this return value and the organizer's terminal


async def load_event(
    db: AsyncSession, parsed: ListFile, videos: Mapping[str, bytes], *, secret: str
) -> list[IssuedCode]:
    """Write everything in one transaction, then return the plaintext Codes (never stored).

    `videos` holds the already-converted MP4 of each participant, keyed by casefolded
    display_name: conversion is slow and can fail, so it happens before this transaction opens.
    Other events may exist side by side. Raises EventExistsError, writing nothing, if one has the
    same name (compared with casefold in Python, not SQL lower()). Codes are unique against
    every Participant's hash in the whole database, not only within this list.
    """
    if videos.keys() != {p.display_name.casefold() for p in parsed.participants}:
        raise ValueError("videos must cover exactly the participants in the list")
    wanted = parsed.event.name.casefold()
    if any(name.casefold() == wanted for name in await db.scalars(select(Event.name))):
        raise EventExistsError

    ev = parsed.event
    event = Event(
        name=ev.name,
        budget_amount=ev.budget_amount,
        budget_currency=ev.budget_currency,
        exchange_at=ev.exchange_at_utc,
        place=ev.place,
        after_event_message=ev.after_event_message,
    )
    db.add(event)
    await db.flush()

    # `code_hash` is globally unique (login looks a Code up without knowing the event), so a new
    # Code must differ from every existing Participant's, in every event: redraw on collision.
    taken: set[str] = set(await db.scalars(select(Participant.code_hash)))
    issued: list[IssuedCode] = []
    rows: dict[str, Participant] = {}
    for entry in parsed.participants:
        code = generate_unique_code(lambda c: keyed_hash(secret, c) in taken)
        code_hash = keyed_hash(secret, code)
        taken.add(code_hash)
        issued.append(IssuedCode(entry.display_name, code))
        rows[entry.display_name.casefold()] = Participant(
            event_id=event.id,
            display_name=entry.display_name,
            vocative_name=entry.vocative_name,
            accusative_name=entry.accusative_name,
            note=entry.note,
            code_hash=code_hash,
        )
    db.add_all(rows.values())
    await db.flush()
    db.add_all(
        Animation(
            participant_id=rows[key].id,
            video=video,
            content_type=VIDEO_CONTENT_TYPE,
            byte_size=len(video),
        )
        for key, video in videos.items()
    )
    # Partner links after insert (self-referencing FK); the list was validated as symmetric.
    for entry in parsed.participants:
        if entry.partner is not None:
            rows[entry.display_name.casefold()].partner_id = rows[entry.partner.casefold()].id
    await db.commit()
    logger.info("event_loaded", participants=len(issued))
    return issued
