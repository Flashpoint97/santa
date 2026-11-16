"""Login-code exchange and bearer-token resolution. No FastAPI imports.

`authenticate` is the single place a bearer token becomes a Principal. Idle expiry and the
activity bump live here, so every authenticated route inherits them.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Participant, Session
from app.services.rate_limit import RateLimiter
from app.services.security import keyed_hash

logger = structlog.get_logger(__name__)

LOGIN_FAILURE_GLOBAL_KEY = "login-failure:global"


def login_failure_ip_key(ip: str) -> str:
    return f"login-failure:ip:{ip}"


@dataclass(frozen=True)
class Principal:
    """The verified caller. Built only from a Session row, never from request input."""

    participant_id: uuid.UUID
    event_id: uuid.UUID
    session_id: uuid.UUID


@dataclass(frozen=True)
class LoginLimits:
    """Failed-login limits: per client IP and site-wide, each over its own sliding window."""

    per_ip: int
    per_ip_window: timedelta
    site_wide: int
    site_wide_window: timedelta


class TooManyLoginAttempts(Exception):
    """A failed-login limit is exhausted. Carries no detail on purpose."""


def hash_token(secret: str, token: str) -> str:
    # Prefix keeps the token hash space apart from the code hashes made with the same key.
    return keyed_hash(secret, f"session-token:{token}")


async def login_with_code(
    db: AsyncSession, *, secret: str, code: str, ip: str, limits: LoginLimits
) -> str | None:
    """Exchange a Code for a new Session token, or None if no Participant has that Code.

    Raises TooManyLoginAttempts when this IP or the site as a whole has used up its failed
    attempts; the Code is then not even checked, so a guesser learns nothing while locked out.
    Only failures are recorded. The per-key advisory locks are held from the limit check until
    the commit, which makes check + record atomic: concurrent wrong guesses cannot together
    exceed a limit (see rate_limit.py). Stores only the token's hash. Commits in every outcome.
    """
    limiter = RateLimiter(db, secret=secret)
    ip_key = login_failure_ip_key(ip)
    await limiter.lock(ip_key, LOGIN_FAILURE_GLOBAL_KEY)
    if (
        await limiter.count(ip_key, window=limits.per_ip_window) >= limits.per_ip
        or await limiter.count(LOGIN_FAILURE_GLOBAL_KEY, window=limits.site_wide_window)
        >= limits.site_wide
    ):
        await db.rollback()  # releases the locks
        logger.warning("login_rate_limited")
        raise TooManyLoginAttempts

    participant = await db.scalar(
        select(Participant).where(Participant.code_hash == keyed_hash(secret, code))
    )
    if participant is None:
        limiter.record(ip_key)
        limiter.record(LOGIN_FAILURE_GLOBAL_KEY)
        await limiter.prune(older_than=max(limits.per_ip_window, limits.site_wide_window))
        await db.commit()  # persist the failure before the caller answers 400
        logger.info("login_failed")
        return None
    token = secrets.token_urlsafe(32)
    db.add(Session(participant_id=participant.id, token_hash=hash_token(secret, token)))
    participant.has_logged_in = True
    await db.commit()
    logger.info("login_succeeded")  # no participant identity in logs
    return token


async def authenticate(
    db: AsyncSession, *, secret: str, token: str, idle_timeout: timedelta
) -> Principal | None:
    """Resolve a bearer token to its Principal, or None (fail closed).

    A Session is valid only while now - last_activity_at <= idle_timeout. The validity check
    and the activity bump are one UPDATE, so a request racing the expiry cannot revive a
    Session. An expired Session is deleted, not just rejected. Commits.
    """
    if not token:
        return None
    token_hash = hash_token(secret, token)
    now = datetime.now(UTC)
    cutoff = now - idle_timeout
    row = (
        await db.execute(
            update(Session)
            .where(Session.token_hash == token_hash, Session.last_activity_at >= cutoff)
            .values(last_activity_at=now)
            .returning(Session.id, Session.participant_id)
        )
    ).one_or_none()
    if row is None:
        # Unknown token: deletes nothing. Expired token: deleted here.
        await db.execute(
            delete(Session).where(
                Session.token_hash == token_hash, Session.last_activity_at < cutoff
            )
        )
        await db.commit()
        return None
    event_id = await db.scalar(
        select(Participant.event_id).where(Participant.id == row.participant_id)
    )
    await db.commit()
    if event_id is None:  # cannot happen (FK cascade); fail closed anyway
        return None
    return Principal(participant_id=row.participant_id, event_id=event_id, session_id=row.id)


async def logout(db: AsyncSession, principal: Principal) -> None:
    """End the caller's own Session (the Participant's other Sessions are untouched)."""
    await db.execute(delete(Session).where(Session.id == principal.session_id))
    await db.commit()
    logger.info("logout")
