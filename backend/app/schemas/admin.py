import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.schemas.auth import NormalizedEmail
from app.schemas.me import EventOut

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
EventName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class EventStatusOut(BaseModel):
    event: EventOut
    participant_count: int
    verified_count: int


class EventPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: EventName | None = None
    budget_amount: Annotated[Decimal, Field(ge=0, le=100_000, decimal_places=2)] | None = None
    budget_currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None
    gift_deadline: date | None = None


class ParticipantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    email: NormalizedEmail


class ParticipantsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    participants: Annotated[list[ParticipantIn], Field(min_length=1, max_length=200)]

    @field_validator("participants")
    @classmethod
    def _unique_emails(cls, v: list[ParticipantIn]) -> list[ParticipantIn]:
        emails = [p.email for p in v]
        if len(emails) != len(set(emails)):
            raise ValueError("duplicate email in list")
        return v


class ParticipantOut(BaseModel):
    """Admin view of a participant. Contains no assignment information by design."""

    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    email: str
    is_admin: bool
    verified_at: datetime | None


class ExclusionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    giver_id: uuid.UUID
    receiver_id: uuid.UUID
    mutual: bool = True  # couples usually exclude each other both ways


class ExclusionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    giver_id: uuid.UUID
    receiver_id: uuid.UUID


class DrawIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    redraw: bool = False  # must be explicit to replace an existing draw
    notify: bool = True  # email everyone "the draw is done, log in"


class DrawOut(BaseModel):
    participant_count: int
    drawn_at: datetime
