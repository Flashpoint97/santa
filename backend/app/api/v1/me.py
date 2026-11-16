from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import get_participant_store
from app.schemas.me import AssignmentOut, EventOut, MeOut
from app.services.stores import NotFoundError, ParticipantStore

router = APIRouter(prefix="/me", tags=["me"])


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    response_model=MeOut,
    summary="My details, my event, and (after the draw) who I buy for",
)
async def get_me(store: Annotated[ParticipantStore, Depends(get_participant_store)]) -> MeOut:
    try:
        view = await store.me()
    except NotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found") from None
    e = view.event
    return MeOut(
        name=view.participant.name,
        is_admin=view.participant.is_admin,
        event=EventOut(
            name=e.name,
            budget_amount=e.budget_amount,
            budget_currency=e.budget_currency,
            gift_deadline=e.gift_deadline,
            drawn_at=e.drawn_at,
        ),
        assignment=AssignmentOut(receiver_name=view.receiver_name) if view.receiver_name else None,
    )
