"""Run the draw for one event and persist it. No FastAPI imports.

Everything is written in one transaction under a lock on that event's row, so concurrent runs
serialise and a failed or impossible draw leaves the previous state untouched. Other events
are never read or locked. Assignments are never logged or returned: callers only learn how many
people took part.
"""

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Assignment, Participant, utcnow
from app.services.draw import DrawImpossibleError, draw
from app.services.events import NoEventError, resolve_event

logger = structlog.get_logger(__name__)

__all__ = ["AlreadyDrawnError", "DrawImpossibleError", "NoEventError", "run_draw"]


class AlreadyDrawnError(Exception):
    """The draw has already happened; replacing it takes an explicit re-draw."""


async def run_draw(db: AsyncSession, *, redraw: bool, event_name: str | None = None) -> int:
    """Draw the chosen event (see `resolve_event`) and commit; return the number of participants.

    Raises NoEventError, UnknownEventError, AmbiguousEventError, AlreadyDrawnError (unless
    `redraw`) or DrawImpossibleError; each leaves the database unchanged. A re-draw replaces the
    old Assignments in the same transaction and resets every Participant's first-reveal flag,
    because their receiver changed.
    """
    event = await resolve_event(db, event_name, lock=True)
    if event.drawn_at is not None and not redraw:
        raise AlreadyDrawnError

    people = (
        await db.scalars(
            select(Participant).where(Participant.event_id == event.id).order_by(Participant.id)
        )
    ).all()
    # Partners never draw each other, in both directions.
    exclusions = [
        pair
        for p in people
        if p.partner_id is not None
        for pair in ((p.id, p.partner_id), (p.partner_id, p.id))
    ]
    pairs = draw([p.id for p in people], exclusions)  # raises DrawImpossibleError

    await db.execute(delete(Assignment).where(Assignment.event_id == event.id))
    db.add_all(
        Assignment(event_id=event.id, giver_id=giver, receiver_id=receiver)
        for giver, receiver in pairs.items()
    )
    if redraw:
        await db.execute(
            update(Participant)
            .where(Participant.event_id == event.id)
            .values(first_revealed_at=None)
        )
    event.drawn_at = utcnow()
    await db.commit()
    logger.info("draw_completed", participants=len(people), redraw=redraw)
    return len(people)
