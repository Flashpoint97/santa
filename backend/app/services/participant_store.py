"""Per-request data access bound to the verified Principal.

The scope (participant_id, event_id) is fixed at construction from the verified Session and
applied in the WHERE clause of every query; no method takes an id from the caller. Reveal and
animation reads (tickets 04/05) are added here so they inherit the same scope.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event, Participant
from app.schemas.me import EventOut, MeResponse
from app.services.sessions import Principal


class ParticipantStore:
    def __init__(self, db: AsyncSession, principal: Principal) -> None:
        self._db = db
        self._participant_id = principal.participant_id
        self._event_id = principal.event_id

    async def get_me(self) -> MeResponse | None:
        row = (
            await self._db.execute(
                select(Participant, Event)
                .join(Event, Event.id == Participant.event_id)
                .where(
                    Participant.id == self._participant_id,
                    Participant.event_id == self._event_id,
                )
            )
        ).one_or_none()
        if row is None:
            return None
        participant, event = row
        return MeResponse(
            display_name=participant.display_name,
            vocative_name=participant.vocative_name,
            personal_note=participant.note or None,
            event=EventOut(
                name=event.name,
                budget_amount=event.budget_amount,
                budget_currency=event.budget_currency,
                exchange_at=event.exchange_at,
                place=event.place,
                after_event_message=event.after_event_message,
            ),
            drawn=event.drawn_at is not None,
            has_revealed=participant.first_revealed_at is not None,
        )
