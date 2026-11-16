"""The Countdown payload: only the caller's own data plus the one shared event."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class EventOut(BaseModel):
    name: str
    budget_amount: Decimal | None  # serialised as a decimal string, e.g. "50.00"
    budget_currency: str
    exchange_at: datetime  # always UTC; the frontend renders it in Europe/Zagreb
    place: str
    after_event_message: str


class MeResponse(BaseModel):
    display_name: str
    vocative_name: str
    personal_note: str | None
    event: EventOut
    drawn: bool
    has_revealed: bool
