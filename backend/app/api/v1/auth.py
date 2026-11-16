from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from app.dependencies import client_ip, get_auth_service, get_email_sender, get_token_service
from app.metrics import LOGIN_CODE_REQUESTS, LOGIN_VERIFICATIONS
from app.schemas.auth import RequestCodeIn, RequestCodeOut, TokenOut, VerifyCodeIn
from app.services.auth import AuthService, RequestCodeOutcome
from app.services.email import EmailSender
from app.services.security import TokenService
from app.services.tasks import send_login_code

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/request-code",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RequestCodeOut,
    summary="Request a one-time login code",
    description="Same response whether or not the email is on the list, so the list "
    "can't be probed. The email itself is sent in the background.",
    responses={429: {"description": "Too many requests"}},
)
async def request_code(
    body: RequestCodeIn,
    request: Request,
    background: BackgroundTasks,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    sender: Annotated[EmailSender, Depends(get_email_sender)],
) -> RequestCodeOut:
    outcome, to_send = await auth.request_code(body.email, client_ip(request))
    if outcome is RequestCodeOutcome.RATE_LIMITED:
        LOGIN_CODE_REQUESTS.labels(outcome="rate_limited").inc()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try later.")
    LOGIN_CODE_REQUESTS.labels(outcome="accepted").inc()
    if to_send is not None:
        background.add_task(send_login_code, sender, to_send)
    return RequestCodeOut()


@router.post(
    "/verify-code",
    status_code=status.HTTP_200_OK,
    response_model=TokenOut,
    summary="Exchange email + code for a session token",
    responses={400: {"description": "Invalid or expired code"}},
)
async def verify_code(
    body: VerifyCodeIn,
    request: Request,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
) -> TokenOut:
    token = await auth.verify_code(body.email, body.code, client_ip(request))
    if token is None:
        LOGIN_VERIFICATIONS.labels(outcome="failure").inc()
        # One message for every failure reason (unknown email, wrong, expired, used, limited).
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired code.")
    LOGIN_VERIFICATIONS.labels(outcome="success").inc()
    return TokenOut(access_token=token, expires_in=tokens.ttl_seconds)
