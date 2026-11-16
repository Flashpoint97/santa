"""Store-bound data access. Each store is built per request with its scope bound in from the
*verified token*, never from request input, and applies that scope in every query.
Anything outside the scope is simply "not found", so the API can return 404 and not leak
whether it exists.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import Assignment, Event, Exclusion, Participant
from app.services.draw import draw

logger = structlog.get_logger(__name__)


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


# --------------------------------------------------------------------------- participant view


@dataclass(frozen=True, slots=True)
class MyView:
    participant: Participant
    event: Event
    receiver_name: str | None


class ParticipantStore:
    """What a logged-in participant may read: themselves, their event, their own assignment."""

    def __init__(
        self, session: AsyncSession, *, participant_id: uuid.UUID, event_id: uuid.UUID
    ) -> None:
        self._session = session
        self._participant_id = participant_id
        self._event_id = event_id

    async def me(self) -> MyView:
        row = (
            await self._session.execute(
                select(Participant, Event)
                .join(Event, Event.id == Participant.event_id)
                .where(Participant.id == self._participant_id, Event.id == self._event_id)
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError
        participant, event = row

        # Only ever the row where *I* am the giver.
        receiver = aliased(Participant)
        receiver_name = await self._session.scalar(
            select(receiver.name)
            .select_from(Assignment)
            .join(receiver, receiver.id == Assignment.receiver_id)
            .where(
                Assignment.giver_id == self._participant_id,
                Assignment.event_id == self._event_id,
            )
        )
        return MyView(participant, event, receiver_name)


# --------------------------------------------------------------------------- admin


@dataclass(frozen=True, slots=True)
class NewParticipant:
    name: str
    email: str
    is_admin: bool = False


@dataclass(frozen=True, slots=True)
class EventStatus:
    event: Event
    participant_count: int
    verified_count: int


class AdminStore:
    """Organizer operations, scoped to the admin's own event.

    Deliberately has no method that reads assignments: the organizer can't see who drew whom.
    """

    def __init__(self, session: AsyncSession, *, event_id: uuid.UUID, admin_id: uuid.UUID) -> None:
        self._session = session
        self._event_id = event_id
        self._admin_id = admin_id

    async def _event(self, *, lock: bool = False) -> Event:
        stmt = select(Event).where(Event.id == self._event_id)
        if lock:
            # Serialises draws and roster edits against each other.
            stmt = stmt.with_for_update()
        event = await self._session.scalar(stmt)
        if event is None:
            raise NotFoundError
        return event

    async def _editable_event(self) -> Event:
        event = await self._event(lock=True)
        if event.drawn_at is not None:
            raise ConflictError("The draw has already happened. Reset it before editing.")
        return event

    async def _participant_ids(self) -> set[uuid.UUID]:
        rows = await self._session.scalars(
            select(Participant.id).where(Participant.event_id == self._event_id)
        )
        return set(rows)

    # ---- event

    async def status(self) -> EventStatus:
        event = await self._event()
        total, verified = (
            await self._session.execute(
                select(func.count(), func.count(Participant.verified_at)).where(
                    Participant.event_id == self._event_id
                )
            )
        ).one()
        return EventStatus(event, total, verified)

    async def update_event(
        self,
        *,
        name: str | None,
        budget_amount: Decimal | None,
        budget_currency: str | None,
        gift_deadline: date | None,
        fields_set: set[str],
    ) -> EventStatus:
        event = await self._event(lock=True)
        # `fields_set` distinguishes "set to null" from "not sent" for a PATCH.
        if "name" in fields_set and name is not None:
            event.name = name
        if "budget_amount" in fields_set:
            event.budget_amount = budget_amount
        if "budget_currency" in fields_set and budget_currency is not None:
            event.budget_currency = budget_currency
        if "gift_deadline" in fields_set:
            event.gift_deadline = gift_deadline
        await self._session.commit()
        return await self.status()

    # ---- participants

    async def list_participants(self) -> list[Participant]:
        rows = await self._session.scalars(
            select(Participant)
            .where(Participant.event_id == self._event_id)
            .order_by(Participant.name)
        )
        return list(rows)

    async def add_participants(self, people: list[NewParticipant]) -> list[Participant]:
        await self._editable_event()
        created = [
            Participant(event_id=self._event_id, name=p.name, email=p.email, is_admin=p.is_admin)
            for p in people
        ]
        self._session.add_all(created)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError("One of these emails is already on the list.") from exc
        return created

    async def remove_participant(self, participant_id: uuid.UUID) -> None:
        await self._editable_event()
        if participant_id == self._admin_id:
            raise ConflictError("You can't remove yourself.")
        participant = await self._session.scalar(
            select(Participant).where(
                Participant.id == participant_id, Participant.event_id == self._event_id
            )
        )
        if participant is None:
            raise NotFoundError
        await self._session.delete(participant)  # cascades codes + exclusions in one tx
        await self._session.commit()

    # ---- exclusions

    async def list_exclusions(self) -> list[Exclusion]:
        rows = await self._session.scalars(
            select(Exclusion).where(Exclusion.event_id == self._event_id)
        )
        return list(rows)

    async def add_exclusion(
        self, giver_id: uuid.UUID, receiver_id: uuid.UUID, *, mutual: bool
    ) -> list[Exclusion]:
        await self._editable_event()
        if giver_id == receiver_id:
            raise ConflictError("A person can't be excluded from themselves.")
        # Both ends must belong to *this* event; otherwise it's "not found".
        if not {giver_id, receiver_id} <= await self._participant_ids():
            raise NotFoundError
        pairs = [(giver_id, receiver_id)] + ([(receiver_id, giver_id)] if mutual else [])
        existing = {(e.giver_id, e.receiver_id) for e in await self.list_exclusions()}
        created = [
            Exclusion(event_id=self._event_id, giver_id=g, receiver_id=r)
            for g, r in pairs
            if (g, r) not in existing
        ]
        self._session.add_all(created)
        await self._session.commit()
        return created

    async def remove_exclusion(self, exclusion_id: uuid.UUID) -> None:
        await self._editable_event()
        exclusion = await self._session.scalar(
            select(Exclusion).where(
                Exclusion.id == exclusion_id, Exclusion.event_id == self._event_id
            )
        )
        if exclusion is None:
            raise NotFoundError
        await self._session.delete(exclusion)
        await self._session.commit()

    # ---- draw

    async def run_draw(self, *, redraw: bool) -> int:
        """Draw (or atomically re-draw) in one transaction. Returns the participant count.

        Raises DrawImpossibleError (from services.draw) when exclusions can't be satisfied;
        in that case nothing is written.
        """
        event = await self._event(lock=True)
        if event.drawn_at is not None and not redraw:
            raise ConflictError("The draw has already happened.")

        people = sorted(await self._participant_ids())
        exclusions = [(e.giver_id, e.receiver_id) for e in await self.list_exclusions()]
        try:
            pairs = draw(people, exclusions)
        except Exception:
            await self._session.rollback()
            raise

        await self._session.execute(delete(Assignment).where(Assignment.event_id == event.id))
        self._session.add_all(
            Assignment(event_id=event.id, giver_id=g, receiver_id=r) for g, r in pairs.items()
        )
        event.drawn_at = datetime.now(UTC)
        await self._session.commit()
        # Never log pairs: only that a draw happened and how big it was.
        logger.info("draw_completed", event_id=str(event.id), size=len(people), redraw=redraw)
        return len(people)

    async def reset_draw(self) -> None:
        event = await self._event(lock=True)
        await self._session.execute(delete(Assignment).where(Assignment.event_id == event.id))
        event.drawn_at = None
        await self._session.commit()
        logger.info("draw_reset", event_id=str(event.id))
