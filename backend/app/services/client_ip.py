"""Which IP address is "the client" for rate limiting.

`X-Forwarded-For` (XFF) is a list each proxy appends to; it never clears what the client sent.
So every entry except those our own proxies appended is attacker-controlled. We therefore never
read the leftmost entry. Instead the deployment says how many proxies sit in front of the app
(`TRUSTED_PROXY_HOPS`) and we take the entry that many places from the RIGHT: with N trusted
proxies, the last N entries were appended by them, and the N-th from the right is the address
the outermost trusted proxy saw connecting, i.e. the real client. Anything to its left is
client-supplied and ignored.

Fails safe: with 0 hops, a missing/short/garbled header, or an entry that is not an IP address,
the socket peer is used. That can only make several clients share one bucket (more lockouts),
never let a client pick its own bucket.
"""

import ipaddress
from collections.abc import Sequence


def resolve_client_ip(peer: str | None, forwarded_for: Sequence[str], trusted_hops: int) -> str:
    """Return the address to rate-limit.

    `peer` is the socket peer, `forwarded_for` the raw values of every `X-Forwarded-For` header
    line (several lines mean the same as one comma-joined line), `trusted_hops` the number of
    proxies we trust to have appended to it.
    """
    fallback = peer or "unknown"
    if trusted_hops < 1:
        return fallback
    entries = [part.strip() for line in forwarded_for for part in line.split(",")]
    if len(entries) < trusted_hops:
        return fallback
    candidate = entries[-trusted_hops]
    try:
        # Normalised, so "::FFFF:1.2.3.4" and "::ffff:1.2.3.4" share a bucket.
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return fallback
