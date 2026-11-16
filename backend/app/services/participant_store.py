"""Per-request data access bound to the verified Principal.

The scope (participant_id, event_id) is fixed at construction from the verified Session and
applied in the WHERE clause of every query; no method takes an id from the caller. Reveal and
the receiver's animation share one join through the caller's own Assignment.
"""

from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Animation, Assignment, Event, Participant, utcnow
from app.schemas.me import EventOut, MeResponse, RevealResponse
from app.services.sessions import Principal


@dataclass(frozen=True)
class AnimationPayload:
    video: bytes
    content_type: str


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

    async def reveal(self) -> RevealResponse | None:
        """The caller's receiver, or None before the draw. Commits.

        `first_time` is decided by one conditional UPDATE: only the call that moves
        first_revealed_at from NULL reports True, so concurrent calls cannot both win.
        """
        receiver = (
            await self._db.execute(
                select(Participant.display_name, Participant.accusative_name)
                .join(Assignment, Assignment.receiver_id == Participant.id)
                .where(
                    Assignment.giver_id == self._participant_id,
                    Assignment.event_id == self._event_id,
                )
            )
        ).one_or_none()
        if receiver is None:
            return None
        result = await self._db.execute(
            update(Participant)
            .where(
                Participant.id == self._participant_id,
                Participant.event_id == self._event_id,
                Participant.first_revealed_at.is_(None),
            )
            .values(first_revealed_at=utcnow())
        )
        await self._db.commit()
        return RevealResponse(
            receiver_display_name=receiver.display_name,
            receiver_accusative_name=receiver.accusative_name,
            first_time=cast(CursorResult[Any], result).rowcount == 1,
        )

    async def receiver_animation(self) -> AnimationPayload | None:
        """The clip of the caller's own receiver, or None (no draw, or no clip stored).

        Resolved through the caller's Assignment, so there is no id to guess. This is the only
        query that loads the video blob.
        """
        row = (
            await self._db.execute(
                select(Animation.video, Animation.content_type)
                .join(Assignment, Assignment.receiver_id == Animation.participant_id)
                .where(
                    Assignment.giver_id == self._participant_id,
                    Assignment.event_id == self._event_id,
                )
            )
        ).one_or_none()
        if row is None:
            return None
        return AnimationPayload(video=row.video, content_type=row.content_type)
