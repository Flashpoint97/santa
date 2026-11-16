"""Request/response models for login. Validated at the edge: exactly 6 digits, no extra keys."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# ASCII digits only: `\d` would also accept other Unicode digits.
SixDigitCode = Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: SixDigitCode


class SessionResponse(BaseModel):
    token: str = Field(description="Opaque bearer token; only its hash is stored server-side.")
    idle_timeout_seconds: int
