"""Depends-able providers. Services are plain classes wired here; they never import FastAPI.

Auth fails closed: a missing, malformed or unknown bearer token is always a 401, and there is
no switch that disables it. Routes get a store already bound to the verified Principal.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.services.client_ip import resolve_client_ip
from app.services.participant_store import ParticipantStore
from app.services.sessions import Principal, authenticate

DbSession = Annotated[AsyncSession, Depends(get_db)]

# auto_error=False so we answer 401 ourselves (the built-in answers 403 on a missing header).
_bearer = HTTPBearer(auto_error=False)


def client_ip(request: Request) -> str:
    # uvicorn runs with --no-proxy-headers, so request.client is the socket peer (Render's
    # proxy). The real client comes from the right-hand end of X-Forwarded-For, counted by
    # TRUSTED_PROXY_HOPS; the client-controlled left part is never read. See services/client_ip.py.
    return resolve_client_ip(
        request.client.host if request.client else None,
        request.headers.getlist("x-forwarded-for"),
        settings.trusted_proxy_hops,
    )


ClientIp = Annotated[str, Depends(client_ip)]


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
        db,
        secret=settings.code_hmac_secret.get_secret_value(),
        token=credentials.credentials,
        idle_timeout=timedelta(minutes=settings.session_idle_timeout_minutes),
    )
    if principal is None:
        raise unauthorized
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def get_participant_store(db: DbSession, principal: CurrentPrincipal) -> ParticipantStore:
    return ParticipantStore(db, principal)


Store = Annotated[ParticipantStore, Depends(get_participant_store)]
