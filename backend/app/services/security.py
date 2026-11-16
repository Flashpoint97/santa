"""Login-code generation and keyed hashing. No FastAPI imports."""

import hashlib
import hmac
import secrets


def generate_login_code() -> str:
    """6-digit code from the OS CSPRNG."""
    return f"{secrets.randbelow(1_000_000):06d}"


def keyed_hash(secret: str, value: str) -> str:
    """HMAC-SHA256. A plain hash of a 6-digit code could be brute-forced offline from a DB
    dump in milliseconds; the server-side key prevents that."""
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def hashes_match(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
