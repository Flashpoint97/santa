"""Login-code hashing and session tokens. No FastAPI imports."""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

_ALGORITHM = "HS256"
_AUDIENCE = "santa-api"


def generate_login_code() -> str:
    """6-digit code from the OS CSPRNG."""
    return f"{secrets.randbelow(1_000_000):06d}"


def keyed_hash(secret: str, value: str) -> str:
    """HMAC-SHA256. A plain hash of a 6-digit code could be brute-forced offline from a DB
    dump in milliseconds; the server-side key prevents that."""
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def hashes_match(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


@dataclass(frozen=True, slots=True)
class TokenClaims:
    participant_id: uuid.UUID
    event_id: uuid.UUID


class TokenService:
    def __init__(self, secret: str, ttl: timedelta) -> None:
        self._secret = secret
        self._ttl = ttl

    @property
    def ttl_seconds(self) -> int:
        return int(self._ttl.total_seconds())

    def issue(self, claims: TokenClaims) -> str:
        now = datetime.now(UTC)
        payload = {
            "sub": str(claims.participant_id),
            "evt": str(claims.event_id),
            "aud": _AUDIENCE,
            "iat": now,
            "exp": now + self._ttl,
        }
        return jwt.encode(payload, self._secret, algorithm=_ALGORITHM)

    def verify(self, token: str) -> TokenClaims | None:
        """Return claims, or None for *any* problem. Callers treat None as unauthenticated."""
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[_ALGORITHM],  # pinned: never trust the token's own `alg`
                audience=_AUDIENCE,
                options={"require": ["sub", "evt", "exp", "aud"]},
            )
            return TokenClaims(uuid.UUID(payload["sub"]), uuid.UUID(payload["evt"]))
        except (jwt.PyJWTError, ValueError, KeyError, TypeError):
            return None
