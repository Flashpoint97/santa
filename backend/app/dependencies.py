"""Depends-able providers. Services are plain classes wired here; they never import FastAPI.

Auth fails closed: a missing, malformed or unknown bearer token is always a 401, and there is
no switch that disables it. Routes get a store already bound to the verified Principal.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.services.participant_store import ParticipantStore
from app.services.sessions import Principal, authenticate

DbSession = Annotated[AsyncSession, Depends(get_db)]

# auto_error=False so we answer 401 ourselves (the built-in answers 403 on a missing header).
_bearer = HTTPBearer(auto_error=False)


def client_ip(request: Request) -> str:
    # Behind Render's proxy, uvicorn --proxy-headers rewrites request.client from
    # X-Forwarded-For. We never parse that header ourselves (it's client-controlled).
    return request.client.host if request.client else "unknown"


async def get_principal(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    principal = await authenticate(
        db, secret=settings.code_hmac_secret.get_secret_value(), token=credentials.credentials
    )
    if principal is None:
        raise unauthorized
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def get_participant_store(db: DbSession, principal: CurrentPrincipal) -> ParticipantStore:
    return ParticipantStore(db, principal)


Store = Annotated[ParticipantStore, Depends(get_participant_store)]
