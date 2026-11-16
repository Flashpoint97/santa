"""Parse and validate the organizer's private list file. No FastAPI imports, no database.

Everything is checked here, before the loader writes a single row. Error messages name the
field and the problem but never echo values back, so a typo in a note cannot end up in a log.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)

ZAGREB = ZoneInfo("Europe/Zagreb")

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class ListFileError(Exception):
    """The list file is unusable. `problems` is a list of human-readable lines."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


class EventBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    budget_amount: Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]
    budget_currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] = "EUR"
    # Europe/Zagreb wall-clock time, no UTC offset: "2026-12-25T12:00".
    exchange_at: datetime
    place: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    after_event_message: Annotated[str, StringConstraints(min_length=1)]

    @field_validator("exchange_at")
    @classmethod
    def _local_zagreb_time(cls, v: datetime) -> datetime:
        if v.tzinfo is not None:
            raise ValueError("must be Europe/Zagreb local time without a UTC offset or 'Z'")
        # A wall-clock time inside the spring DST gap never happens; refuse it.
        if v.replace(tzinfo=ZAGREB).astimezone(UTC).astimezone(ZAGREB).replace(tzinfo=None) != v:
            raise ValueError("this local time does not exist in Europe/Zagreb (DST gap)")
        return v

    @property
    def exchange_at_utc(self) -> datetime:
        return self.exchange_at.replace(tzinfo=ZAGREB).astimezone(UTC)


class ParticipantEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: Name
    vocative_name: Name
    accusative_name: Name
    partner: Name | None = None  # the partner's display_name; must point back
    note: str = ""
    gif: str | None = None  # path relative to the list file; converted by a later step


class ListFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: EventBlock
    participants: Annotated[list[ParticipantEntry], Field(min_length=2)]


def _cross_check(parsed: ListFile) -> list[str]:
    problems: list[str] = []
    people = parsed.participants
    by_name: dict[str, ParticipantEntry] = {}
    for i, p in enumerate(people):
        key = p.display_name.casefold()
        if key in by_name:
            problems.append(f"participants[{i}].display_name: duplicate name")
        by_name[key] = p
    for i, p in enumerate(people):
        if p.partner is None:
            continue
        if p.partner.casefold() == p.display_name.casefold():
            problems.append(f"participants[{i}].partner: cannot be the person themselves")
            continue
        other = by_name.get(p.partner.casefold())
        if other is None:
            problems.append(f"participants[{i}].partner: no participant with that name")
        elif other.partner is None or other.partner.casefold() != p.display_name.casefold():
            problems.append(f"participants[{i}].partner: link is not symmetric")
    return problems


def _format(err: ValidationError) -> list[str]:
    return [
        f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}"
        for e in err.errors(include_input=False, include_url=False)
    ]


def parse_list_file(raw: str | bytes) -> ListFile:
    """Return the validated list or raise ListFileError listing every problem found."""
    try:
        data: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ListFileError([f"not valid JSON ({type(exc).__name__})"]) from None
    try:
        parsed = ListFile.model_validate(data)
    except ValidationError as err:
        raise ListFileError(_format(err)) from None
    problems = _cross_check(parsed)
    if problems:
        raise ListFileError(problems)
    return parsed
