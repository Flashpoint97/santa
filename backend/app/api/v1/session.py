from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status

from app.config import settings
from app.dependencies import ClientIp, CurrentPrincipal, DbSession
from app.schemas.session import LoginRequest, SessionResponse
from app.services.sessions import LoginLimits, TooManyLoginAttempts, login_with_code, logout

router = APIRouter(tags=["session"])

# One message for every failed login so the response reveals nothing to a guesser.
INVALID_CODE_MESSAGE = "Neispravan kod."
TOO_MANY_ATTEMPTS_MESSAGE = "Previše pokušaja. Pokušaj ponovno kasnije."

_UNAUTHORIZED: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing, unknown or expired token"}
}


@router.post(
    "/session",
    status_code=status.HTTP_200_OK,
    response_model=SessionResponse,
    summary="Exchange a 6-digit Code for a Session token",
    responses={
        400: {"description": "Invalid Code (one generic message)"},
        429: {"description": "Too many failed attempts from this IP or site-wide"},
    },
)
async def create_session(body: LoginRequest, db: DbSession, ip: ClientIp) -> SessionResponse:
    limits = LoginLimits(
        per_ip=settings.login_failures_per_ip,
        per_ip_window=timedelta(minutes=settings.login_failures_per_ip_window_minutes),
        site_wide=settings.login_failures_global,
        site_wide_window=timedelta(minutes=settings.login_failures_global_window_minutes),
    )
    try:
        token = await login_with_code(
            db,
            secret=settings.code_hmac_secret.get_secret_value(),
            code=body.code,
            ip=ip,
            limits=limits,
        )
    except TooManyLoginAttempts:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, detail=TOO_MANY_ATTEMPTS_MESSAGE
        ) from None
    if token is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=INVALID_CODE_MESSAGE)
    return SessionResponse(
        token=token, idle_timeout_seconds=settings.session_idle_timeout_minutes * 60
    )


@router.post(
    "/session/heartbeat",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Keep the Session alive while the user is active",
    description="Authentication itself bumps last_activity_at; the route has nothing else to do.",
    responses=_UNAUTHORIZED,
)
async def heartbeat(_principal: CurrentPrincipal) -> Response:
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/session",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Log out (end this Session)",
    responses=_UNAUTHORIZED,
)
async def delete_session(db: DbSession, principal: CurrentPrincipal) -> Response:
    await logout(db, principal)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
