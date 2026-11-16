from fastapi import APIRouter, HTTPException, status

from app.config import settings
from app.dependencies import DbSession
from app.schemas.session import LoginRequest, SessionResponse
from app.services.sessions import login_with_code

router = APIRouter(tags=["session"])

# One message for every failed login so the response reveals nothing to a guesser.
INVALID_CODE_MESSAGE = "Neispravan kod."


@router.post(
    "/session",
    status_code=status.HTTP_200_OK,
    response_model=SessionResponse,
    summary="Exchange a 6-digit Code for a Session token",
    responses={400: {"description": "Invalid Code (one generic message)"}},
)
async def create_session(body: LoginRequest, db: DbSession) -> SessionResponse:
    token = await login_with_code(
        db, secret=settings.code_hmac_secret.get_secret_value(), code=body.code
    )
    if token is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=INVALID_CODE_MESSAGE)
    return SessionResponse(
        token=token, idle_timeout_seconds=settings.session_idle_timeout_minutes * 60
    )
