"""Depends-able providers. Services are plain classes wired here; they never import FastAPI."""

import uuid
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Participant
from app.db.session import get_db
from app.services.auth import AuthPolicy, AuthService
from app.services.email import ConsoleEmailSender, EmailSender, PostmarkEmailSender
from app.services.rate_limit import RateLimiter
from app.services.security import TokenService
from app.services.stores import AdminStore, ParticipantStore

DbSession = Annotated[AsyncSession, Depends(get_db)]


@lru_cache
def get_token_service() -> TokenService:
    return TokenService(
        settings.jwt_secret.get_secret_value(), timedelta(minutes=settings.jwt_ttl_minutes)
    )


@lru_cache
def get_email_sender() -> EmailSender:
    if settings.email_backend == "postmark":
        assert settings.postmark_server_token is not None  # enforced by Settings  # noqa: S101
        return PostmarkEmailSender(
            settings.postmark_server_token.get_secret_value(), settings.email_from
        )
    return ConsoleEmailSender()


def client_ip(request: Request) -> str:
    # Behind Render's proxy, uvicorn --proxy-headers rewrites request.client from
    # X-Forwarded-For. We never parse that header ourselves (it's client-controlled).
    return request.client.host if request.client else "unknown"


def get_auth_service(
    db: DbSession, tokens: Annotated[TokenService, Depends(get_token_service)]
) -> AuthService:
    secret = settings.code_hmac_secret.get_secret_value()
    policy = AuthPolicy(
        code_secret=secret,
        code_ttl=timedelta(minutes=settings.login_code_ttl_minutes),
        max_attempts=settings.login_code_max_attempts,
        requests_per_email=settings.code_requests_per_email,
        requests_per_ip=settings.code_requests_per_ip,
        verifications_per_ip=settings.code_verifications_per_ip,
    )
    limiter = RateLimiter(
        db, secret=secret, window=timedelta(minutes=settings.rate_limit_window_minutes)
    )
    return AuthService(db, policy=policy, tokens=tokens, limiter=limiter)


# --------------------------------------------------------------------------- auth (fail closed)

_bearer = HTTPBearer(auto_error=False)
_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass(frozen=True, slots=True)
class Principal:
    participant_id: uuid.UUID
    event_id: uuid.UUID
    is_admin: bool


async def get_principal(
    db: DbSession,
    tokens: Annotated[TokenService, Depends(get_token_service)],
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise _UNAUTHENTICATED
    claims = tokens.verify(creds.credentials)
    if claims is None:
        raise _UNAUTHENTICATED
    # Re-read the participant: a removed participant's token stops working immediately, and
    # the admin flag comes from the DB rather than from a long-lived claim.
    participant = await db.scalar(
        select(Participant).where(
            Participant.id == claims.participant_id, Participant.event_id == claims.event_id
        )
    )
    if participant is None:
        raise _UNAUTHENTICATED
    return Principal(participant.id, participant.event_id, participant.is_admin)


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


async def require_admin(principal: CurrentPrincipal) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return principal


def get_participant_store(db: DbSession, principal: CurrentPrincipal) -> ParticipantStore:
    # Scope comes from the verified token only.
    return ParticipantStore(
        db, participant_id=principal.participant_id, event_id=principal.event_id
    )


def get_admin_store(
    db: DbSession, admin: Annotated[Principal, Depends(require_admin)]
) -> AdminStore:
    return AdminStore(db, event_id=admin.event_id, admin_id=admin.participant_id)
