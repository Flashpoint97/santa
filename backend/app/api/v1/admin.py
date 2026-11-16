import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.dependencies import get_admin_store, get_email_sender
from app.metrics import DRAWS
from app.schemas.admin import (
    DrawIn,
    DrawOut,
    EventPatchIn,
    EventStatusOut,
    ExclusionIn,
    ExclusionOut,
    ParticipantOut,
    ParticipantsIn,
)
from app.schemas.me import EventOut
from app.services.draw import DrawImpossibleError
from app.services.email import EmailSender
from app.services.stores import (
    AdminStore,
    ConflictError,
    EventStatus,
    NewParticipant,
    NotFoundError,
)
from app.services.tasks import notify_draw_done

router = APIRouter(prefix="/admin", tags=["admin"])
Store = Annotated[AdminStore, Depends(get_admin_store)]


def _status_out(s: EventStatus) -> EventStatusOut:
    e = s.event
    return EventStatusOut(
        event=EventOut(
            name=e.name,
            budget_amount=e.budget_amount,
            budget_currency=e.budget_currency,
            gift_deadline=e.gift_deadline,
            drawn_at=e.drawn_at,
        ),
        participant_count=s.participant_count,
        verified_count=s.verified_count,
    )


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


def _conflict(exc: ConflictError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(exc) or "Conflict")


@router.get("/event", status_code=200, response_model=EventStatusOut, summary="Event status")
async def get_event(store: Store) -> EventStatusOut:
    return _status_out(await store.status())


@router.patch(
    "/event", status_code=200, response_model=EventStatusOut, summary="Edit name/budget/deadline"
)
async def patch_event(body: EventPatchIn, store: Store) -> EventStatusOut:
    s = await store.update_event(
        name=body.name,
        budget_amount=body.budget_amount,
        budget_currency=body.budget_currency,
        gift_deadline=body.gift_deadline,
        fields_set=body.model_fields_set,
    )
    return _status_out(s)


@router.get("/participants", status_code=200, response_model=list[ParticipantOut], summary="Roster")
async def list_participants(store: Store) -> list[ParticipantOut]:
    return [ParticipantOut.model_validate(p) for p in await store.list_participants()]


@router.post(
    "/participants",
    status_code=status.HTTP_201_CREATED,
    response_model=list[ParticipantOut],
    summary="Add participants (only before the draw)",
)
async def add_participants(body: ParticipantsIn, store: Store) -> list[ParticipantOut]:
    try:
        created = await store.add_participants(
            [NewParticipant(name=p.name, email=p.email) for p in body.participants]
        )
    except ConflictError as exc:
        raise _conflict(exc) from None
    return [ParticipantOut.model_validate(p) for p in created]


@router.delete(
    "/participants/{participant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a participant (only before the draw)",
)
async def remove_participant(participant_id: uuid.UUID, store: Store) -> None:
    try:
        await store.remove_participant(participant_id)
    except NotFoundError:
        raise _not_found() from None
    except ConflictError as exc:
        raise _conflict(exc) from None


@router.get("/exclusions", status_code=200, response_model=list[ExclusionOut], summary="Rules")
async def list_exclusions(store: Store) -> list[ExclusionOut]:
    return [ExclusionOut.model_validate(e) for e in await store.list_exclusions()]


@router.post(
    "/exclusions",
    status_code=status.HTTP_201_CREATED,
    response_model=list[ExclusionOut],
    summary="Add an exclusion (giver must not draw receiver; mutual adds both directions)",
)
async def add_exclusion(body: ExclusionIn, store: Store) -> list[ExclusionOut]:
    try:
        created = await store.add_exclusion(body.giver_id, body.receiver_id, mutual=body.mutual)
    except NotFoundError:
        raise _not_found() from None
    except ConflictError as exc:
        raise _conflict(exc) from None
    return [ExclusionOut.model_validate(e) for e in created]


@router.delete(
    "/exclusions/{exclusion_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove a rule"
)
async def remove_exclusion(exclusion_id: uuid.UUID, store: Store) -> None:
    try:
        await store.remove_exclusion(exclusion_id)
    except NotFoundError:
        raise _not_found() from None
    except ConflictError as exc:
        raise _conflict(exc) from None


@router.post(
    "/draw",
    status_code=status.HTTP_200_OK,
    response_model=DrawOut,
    summary="Run the draw (redraw=true replaces an existing one atomically)",
    description="Returns only the count, never the pairs: the organizer can't see assignments.",
    responses={409: {"description": "Already drawn"}, 422: {"description": "Impossible"}},
)
async def run_draw(
    body: DrawIn,
    store: Store,
    background: BackgroundTasks,
    sender: Annotated[EmailSender, Depends(get_email_sender)],
) -> DrawOut:
    try:
        count = await store.run_draw(redraw=body.redraw)
    except ConflictError as exc:
        DRAWS.labels(outcome="conflict").inc()
        raise _conflict(exc) from None
    except DrawImpossibleError as exc:
        DRAWS.labels(outcome="impossible").inc()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    DRAWS.labels(outcome="success").inc()
    s = await store.status()
    assert s.event.drawn_at is not None  # noqa: S101
    if body.notify:
        background.add_task(notify_draw_done, sender, s.event.id)
    return DrawOut(participant_count=count, drawn_at=s.event.drawn_at)


@router.delete(
    "/draw",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Reset the draw so the roster can be edited again",
)
async def reset_draw(store: Store) -> None:
    await store.reset_draw()
