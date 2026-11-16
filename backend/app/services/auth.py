"""Email -> one-time code -> session token. No FastAPI imports.

Anti-enumeration: `request_code` behaves identically for listed and unlisted emails from the
caller's point of view (same return, rate limits keyed on the submitted address either way).
`verify_code` returns the same failure for "unknown email", "no code", "expired", and "wrong".
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum, auto

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LoginCode, Participant
from app.services.rate_limit import RateLimiter
from app.services.security import (
    TokenClaims,
    TokenService,
    generate_login_code,
    hashes_match,
    keyed_hash,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AuthPolicy:
    code_secret: str
    code_ttl: timedelta
    max_attempts: int
    requests_per_email: int
    requests_per_ip: int
    verifications_per_ip: int


class RequestCodeOutcome(Enum):
    ACCEPTED = auto()  # caller sees the same thing whether or not anything was sent
    RATE_LIMITED = auto()


@dataclass(frozen=True, slots=True)
class CodeToSend:
    """Handed to a background task. Lives only in memory, never persisted or logged."""

    participant_id: uuid.UUID
    email: str
    code: str


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        policy: AuthPolicy,
        tokens: TokenService,
        limiter: RateLimiter,
    ) -> None:
        self._session = session
        self._policy = policy
        self._tokens = tokens
        self._limiter = limiter

    async def _find_participant(self, email: str) -> Participant | None:
        # An email can appear in several events over the years; log into the newest one.
        stmt = (
            select(Participant)
            .where(Participant.email == email)
            .order_by(Participant.created_at.desc())
            .limit(1)
        )
        return await self._session.scalar(stmt)

    async def request_code(
        self, email: str, client_ip: str
    ) -> tuple[RequestCodeOutcome, CodeToSend | None]:
        p = self._policy
        email_ok = await self._limiter.hit(f"code-request:email:{email}", p.requests_per_email)
        ip_ok = await self._limiter.hit(f"code-request:ip:{client_ip}", p.requests_per_ip)
        if not (email_ok and ip_ok):
            await self._session.commit()
            logger.info("login_code_rate_limited", by_email=not email_ok, by_ip=not ip_ok)
            return RequestCodeOutcome.RATE_LIMITED, None

        await self._limiter.prune()
        participant = await self._find_participant(email)
        if participant is None:
            await self._session.commit()
            logger.info("login_code_requested", known=False)
            return RequestCodeOutcome.ACCEPTED, None

        now = datetime.now(UTC)
        # A new code supersedes any previous one: at most one live code per participant.
        await self._session.execute(
            update(LoginCode)
            .where(LoginCode.participant_id == participant.id, LoginCode.used_at.is_(None))
            .values(used_at=now)
        )
        code = generate_login_code()
        self._session.add(
            LoginCode(
                participant_id=participant.id,
                code_hash=keyed_hash(p.code_secret, code),
                expires_at=now + p.code_ttl,
            )
        )
        await self._session.commit()
        logger.info("login_code_requested", known=True, participant_id=str(participant.id))
        return RequestCodeOutcome.ACCEPTED, CodeToSend(participant.id, participant.email, code)

    async def verify_code(self, email: str, code: str, client_ip: str) -> str | None:
        """Return a session token, or None. Every failure looks the same to the caller."""
        p = self._policy
        if not await self._limiter.hit(f"code-verify:ip:{client_ip}", p.verifications_per_ip):
            await self._session.commit()
            logger.info("login_code_verify_rate_limited")
            return None

        participant = await self._find_participant(email)
        if participant is None:
            await self._session.commit()
            return None

        now = datetime.now(UTC)
        stmt = (
            select(LoginCode)
            .where(
                LoginCode.participant_id == participant.id,
                LoginCode.used_at.is_(None),
                LoginCode.expires_at > now,
                LoginCode.attempts < p.max_attempts,
            )
            .order_by(LoginCode.created_at.desc())
            .limit(1)
            .with_for_update()  # serialise concurrent guesses against the same code
        )
        login_code = await self._session.scalar(stmt)
        if login_code is None:
            await self._session.commit()
            logger.info("login_code_verify_failed", reason="no_live_code")
            return None

        if not hashes_match(login_code.code_hash, keyed_hash(p.code_secret, code)):
            login_code.attempts += 1
            if login_code.attempts >= p.max_attempts:
                login_code.used_at = now  # burn it
            await self._session.commit()
            logger.info("login_code_verify_failed", reason="mismatch", attempts=login_code.attempts)
            return None

        login_code.used_at = now  # single use
        if participant.verified_at is None:
            participant.verified_at = now
        await self._session.commit()
        logger.info("login_succeeded", participant_id=str(participant.id))
        return self._tokens.issue(TokenClaims(participant.id, participant.event_id))
