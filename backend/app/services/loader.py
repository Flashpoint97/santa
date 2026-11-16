"""Create the event, participants and Codes from a validated list. No FastAPI imports."""

from dataclasses import dataclass

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event, Participant
from app.services.list_file import ListFile
from app.services.security import generate_login_code, keyed_hash

logger = structlog.get_logger(__name__)


class EventExistsError(Exception):
    """One event per deployment: wipe it before loading another."""


@dataclass(frozen=True)
class IssuedCode:
    display_name: str
    code: str  # plaintext, exists only in this return value and the organizer's terminal


async def load_event(db: AsyncSession, parsed: ListFile, *, secret: str) -> list[IssuedCode]:
    """Write everything in one transaction, then return the plaintext Codes (never stored).

    Raises EventExistsError, writing nothing, if an event is already present.
    """
    if await db.scalar(select(func.count()).select_from(Event)):
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

    # Codes are unique within the event: redraw on collision.
    used: set[str] = set()
    issued: list[IssuedCode] = []
    rows: dict[str, Participant] = {}
    for entry in parsed.participants:
        code = generate_login_code()
        while code in used:
            code = generate_login_code()
        used.add(code)
        issued.append(IssuedCode(entry.display_name, code))
        rows[entry.display_name.casefold()] = Participant(
            event_id=event.id,
            display_name=entry.display_name,
            vocative_name=entry.vocative_name,
            accusative_name=entry.accusative_name,
            note=entry.note,
            code_hash=keyed_hash(secret, code),
        )
    db.add_all(rows.values())
    await db.flush()
    # Partner links after insert (self-referencing FK); the list was validated as symmetric.
    for entry in parsed.participants:
        if entry.partner is not None:
            rows[entry.display_name.casefold()].partner_id = rows[entry.partner.casefold()].id
    await db.commit()
    logger.info("event_loaded", participants=len(issued))
    return issued
