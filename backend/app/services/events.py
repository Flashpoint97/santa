"""Find the Event a CLI command is about. No FastAPI imports.

One deployment can hold several independent Events ("circles"). Commands take an optional
`--event "<name>"`; without it the only Event is used, and with several the organizer must say
which. Names are compared with casefold in Python (SQL lower() is locale-dependent for letters
like č, š, ž, đ). Event names may be shown in the organizer's terminal; nothing else may.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event

__all__ = ["AmbiguousEventError", "NoEventError", "UnknownEventError", "resolve_event"]


class NoEventError(Exception):
    """No event has been loaded."""


class UnknownEventError(Exception):
    """No Event has the requested name."""


class AmbiguousEventError(Exception):
    """Several Events exist and none was named. `names` lists them (organizer's terminal only)."""

    def __init__(self, names: list[str]) -> None:
        super().__init__("several events exist; name one")
        self.names = names


async def resolve_event(db: AsyncSession, name: str | None = None, *, lock: bool = False) -> Event:
    """Return the chosen Event; with `lock`, hold a row lock on it until the transaction ends.

    Raises NoEventError (none loaded), UnknownEventError (no such name) or AmbiguousEventError
    (no name given, several Events). Nothing is changed in any of those cases.
    """
    events = (await db.scalars(select(Event).order_by(Event.name, Event.id))).all()
    if not events:
        raise NoEventError
    if name is None:
        if len(events) > 1:
            raise AmbiguousEventError([e.name for e in events])
        chosen = events[0]
    else:
        wanted = name.strip().casefold()
        chosen = next((e for e in events if e.name.casefold() == wanted), None)
        if chosen is None:
            raise UnknownEventError
    if lock:
        # Re-read just this row with FOR UPDATE; other circles are never locked.
        locked = await db.scalar(
            select(Event)
            .where(Event.id == chosen.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked is None:  # deleted between the two reads
            raise NoEventError
        chosen = locked
    return chosen
