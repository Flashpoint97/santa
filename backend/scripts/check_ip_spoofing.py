"""Black-box check that a deployed API cannot be tricked into a fresh rate-limit bucket.

Usage:  uv run python scripts/check_ip_spoofing.py https://your-api.onrender.com [--limit 10]

Sends wrong Codes to POST /api/v1/session, each with a DIFFERENT forged X-Forwarded-For value.
If the server keys the limit on the real client, the (limit + 1)-th wrong Code is answered 429
although every request claimed another address. If forging worked, no 429 would appear until
the much larger site-wide limit.

Side effects (be aware): every request is a FAILED login recorded against your real IP and
site-wide, and your IP stays locked out for LOGIN_FAILURES_PER_IP_WINDOW_MINUTES (15 by default).
Run it BEFORE loading the real list (or accept the wait). `wipe` clears the rate-limit rows.
Only made-up Codes (900000 upwards) are sent, never a real one. Standard library only.

Exit code 0 = spoofing did not help (good), 1 = spoofing worked or the result was inconclusive.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

LOGIN_PATH = "/api/v1/session"


def post_wrong_code(base_url: str, code: str, forged_ip: str) -> int:
    request = urllib.request.Request(  # noqa: S310  (scheme is checked in main)
        base_url + LOGIN_PATH,
        data=json.dumps({"code": code}).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Forwarded-For": forged_ip,
            "X-Real-IP": forged_ip,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:  # noqa: S310
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("api_url", help="API base URL, e.g. https://santa-api.onrender.com")
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="the server's LOGIN_FAILURES_PER_IP (default 10)",
    )
    args = parser.parse_args()
    base_url = args.api_url.rstrip("/")
    if not base_url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        print("Refusing: use an https:// URL (or localhost).", file=sys.stderr)
        return 1

    attempts = args.limit + 3
    statuses: list[int] = []
    for i in range(attempts):
        forged = f"198.51.100.{i + 1}"  # RFC 5737 documentation range: never a real client
        # A real Code could in theory equal one of these (about 1 in 100 000); a 200 is flagged.
        statuses.append(post_wrong_code(base_url, f"{900000 + i:06d}", forged))
    print("statuses:", statuses)

    if any(s == 200 for s in statuses):
        print("A Code was accepted (a made-up Code happened to match). Run again.")
        return 1
    if any(s not in (400, 429) for s in statuses):
        print("Unexpected status codes (is the API URL right and the server awake?).")
        return 1
    first_429 = next((i for i, s in enumerate(statuses) if s == 429), None)
    if first_429 is None:
        print(
            f"FAIL: no 429 after {attempts} wrong Codes with forged addresses. The forged "
            "X-Forwarded-For chose the rate-limit bucket: check TRUSTED_PROXY_HOPS (too high?)."
        )
        return 1
    if first_429 != args.limit:
        print(
            f"INCONCLUSIVE: the first 429 came on attempt {first_429 + 1}, expected "
            f"{args.limit + 1}. Other failures from your IP may already be counted "
            "(wait for the window to pass) or --limit does not match the server."
        )
        return 1
    print(f"OK: attempts 1-{args.limit} got 400, attempt {args.limit + 1} onwards got 429.")
    print("Forged X-Forwarded-For values did not change the rate-limit key.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
