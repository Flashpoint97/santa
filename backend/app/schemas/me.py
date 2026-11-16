from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class EventOut(BaseModel):
    name: str
    budget_amount: Decimal | None
    budget_currency: str
    gift_deadline: date | None
    drawn_at: datetime | None


class AssignmentOut(BaseModel):
    receiver_name: str


class MeOut(BaseModel):
    name: str
    is_admin: bool
    event: EventOut
    # null until the draw has happened
    assignment: AssignmentOut | None
