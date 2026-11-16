from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, StringConstraints


def _lower(v: str) -> str:
    return v.strip().lower()


NormalizedEmail = Annotated[EmailStr, Field(max_length=254), AfterValidator(_lower)]


class RequestCodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: NormalizedEmail


class RequestCodeOut(BaseModel):
    detail: str = "If this email is registered, a code has been sent."


class VerifyCodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: NormalizedEmail
    code: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{6}$")]


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth2 token type, not a secret
    expires_in: int
