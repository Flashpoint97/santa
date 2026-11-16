"""Client IP for rate limits: spoofed X-Forwarded-For entries must never choose the bucket."""

import httpx
import pytest

from app.config import settings
from app.main import app
from app.services.client_ip import resolve_client_ip
from tests.conftest import Seeded

PEER = "10.0.0.9"
LOGIN = "/api/v1/session"


def test_zero_hops_ignores_the_header_entirely() -> None:
    assert resolve_client_ip(PEER, ["203.0.113.7"], 0) == PEER


def test_takes_the_entry_n_from_the_right() -> None:
    # Render shape: client-supplied..., real client, Cloudflare edge, Render internal.
    xff = ["198.51.100.1, 192.0.2.44, 203.0.113.7, 172.71.0.1, 10.1.2.3"]
    assert resolve_client_ip(PEER, xff, 3) == "203.0.113.7"


def test_spoofed_left_entries_are_ignored() -> None:
    real = "203.0.113.7"
    for forged in ("1.1.1.1", "8.8.8.8, 9.9.9.9", "::1", "garbage, 'x"):
        xff = [f"{forged}, {real}, 172.71.0.1, 10.1.2.3"]
        assert resolve_client_ip(PEER, xff, 3) == real


def test_multiple_header_lines_are_one_list() -> None:
    assert resolve_client_ip(PEER, ["1.1.1.1, 203.0.113.7", "172.71.0.1, 10.1.2.3"], 3) == (
        "203.0.113.7"
    )


def test_missing_header_falls_back_to_the_peer() -> None:
    assert resolve_client_ip(PEER, [], 3) == PEER


def test_too_few_entries_fall_back_to_the_peer_not_the_leftmost() -> None:
    # A client that sends nothing extra, or a request that skipped part of the chain.
    assert resolve_client_ip(PEER, ["198.51.100.1, 10.1.2.3"], 3) == PEER


@pytest.mark.parametrize(
    "garbage", ["", " , , ", "not an ip", "1.2.3", "999.1.1.1", "ip:80", "\x00"]
)
def test_garbage_in_the_trusted_slot_falls_back_to_the_peer(garbage: str) -> None:
    assert resolve_client_ip(PEER, [f"{garbage}, 172.71.0.1, 10.1.2.3"], 3) == PEER


def test_ipv6_is_normalised() -> None:
    xff = ["2001:DB8:0:0:0:0:0:1, 172.71.0.1, 10.1.2.3"]
    assert resolve_client_ip(PEER, xff, 3) == "2001:db8::1"


def test_no_peer_and_no_header_is_unknown() -> None:
    assert resolve_client_ip(None, [], 3) == "unknown"
    assert resolve_client_ip(None, [], 0) == "unknown"


def wrong(i: int) -> dict[str, str]:
    return {"code": f"{900000 + i:06d}"}  # never a seeded code (those are 111111...)


async def test_spoofed_header_cannot_dodge_the_per_ip_limit(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "trusted_proxy_hops", 2)
    monkeypatch.setattr(settings, "login_failures_per_ip", 3)
    monkeypatch.setattr(settings, "login_failures_global", 100)
    transport = httpx.ASGITransport(app=app, client=("10.0.0.9", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        statuses = []
        for i in range(5):
            # Same real client behind two trusted proxies; a different forged left entry each time.
            headers = {"X-Forwarded-For": f"198.51.100.{i + 1}, 203.0.113.7, 172.71.0.1"}
            statuses.append((await c.post(LOGIN, json=wrong(i), headers=headers)).status_code)
        assert statuses == [400, 400, 400, 429, 429]
        # A different real client (what the proxy saw) is a different bucket.
        other = {"X-Forwarded-For": "198.51.100.1, 203.0.113.99, 172.71.0.1"}
        assert (await c.post(LOGIN, json=wrong(9), headers=other)).status_code == 400


async def test_header_is_ignored_when_no_proxy_is_trusted(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "trusted_proxy_hops", 0)
    monkeypatch.setattr(settings, "login_failures_per_ip", 2)
    monkeypatch.setattr(settings, "login_failures_global", 100)
    transport = httpx.ASGITransport(app=app, client=("192.0.2.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        statuses = []
        for i in range(4):
            headers = {"X-Forwarded-For": f"198.51.100.{i + 1}"}
            statuses.append((await c.post(LOGIN, json=wrong(i), headers=headers)).status_code)
        assert statuses == [400, 400, 429, 429]
