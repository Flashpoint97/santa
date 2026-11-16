"""Login-code generation and keyed hashing. No FastAPI imports."""

import hashlib
import hmac
import secrets
from collections.abc import Callable


def generate_login_code() -> str:
    """6-digit code from the OS CSPRNG."""
    return f"{secrets.randbelow(1_000_000):06d}"


def generate_unique_code(is_taken: Callable[[str], bool]) -> str:
    """A fresh Code for which `is_taken` is false (redraws on collision; 6 digits, few people)."""
    code = generate_login_code()
    while is_taken(code):
        code = generate_login_code()
    return code


def keyed_hash(secret: str, value: str) -> str:
    """HMAC-SHA256. A plain hash of a 6-digit code could be brute-forced offline from a DB
    dump in milliseconds; the server-side key prevents that."""
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def hashes_match(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
