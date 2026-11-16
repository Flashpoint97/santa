"""Smoke test of the core flow against a real backend and the static frontend build.

    Code (wrong, then right) -> Countdown -> "Otvori svoj poklon" -> Reveal (first-time message,
    <video> with a blob: src) -> reload -> Reveal (reminder message)

The Codes are never hard-coded: the CI job (or you) saves the stdout of
`python -m app.cli load ...` to a file and points SMOKE_CODES_FILE at it. Everything here is
synthetic data.

The first reveal is spent once per seeded database, so re-seed (wipe, load, draw) to re-run.

Environment:
    SMOKE_CODES_FILE   (required) file holding the `load` output, one "  Name: 123456" per line
    SMOKE_FRONTEND_URL (default http://localhost:3000)
    SMOKE_ARTIFACTS    (default e2e/artifacts) where a failure screenshot is written
"""

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, Playwright, expect, sync_playwright

FRONTEND_URL = os.environ.get("SMOKE_FRONTEND_URL", "http://localhost:3000").rstrip("/")
ARTIFACTS = Path(os.environ.get("SMOKE_ARTIFACTS", Path(__file__).parent / "artifacts"))
# The wake gate polls /health with backoff for up to 90 s; a healthy server answers at once.
SLOW_MS = 30_000
# A phone-sized viewport: most people open the link from their phone.
PHONE = {"width": 390, "height": 844}


def load_codes() -> dict[str, str]:
    """Parse "  Osoba A: 123456" lines from the saved output of `app.cli load`."""
    path = os.environ.get("SMOKE_CODES_FILE")
    if not path:
        pytest.fail("SMOKE_CODES_FILE must point at the saved output of `app.cli load`")
    codes = dict(re.findall(r"^\s+(.+): (\d{6})$", Path(path).read_text("utf-8"), re.MULTILINE))
    if len(codes) < 2:
        pytest.fail(f"expected at least 2 'Name: code' lines in {path}, found {len(codes)}")
    return codes


@pytest.fixture(scope="session")
def pw() -> Iterator[Playwright]:
    with sync_playwright() as p:
        yield p


@pytest.fixture
def page(pw: Playwright) -> Iterator[Page]:
    browser = pw.chromium.launch()
    context = browser.new_context(viewport=PHONE, is_mobile=True, has_touch=True)
    page = context.new_page()
    page.set_default_timeout(SLOW_MS)
    yield page
    context.close()
    browser.close()


def test_code_to_countdown_to_reveal(page: Page) -> None:
    codes = load_codes()
    code = next(iter(codes.values()))
    wrong = next(c for c in ("000000", "111111", "222222") if c not in codes.values())

    try:
        # Code screen, behind the wake gate (it shows the input only once /health answers 200).
        page.goto(f"{FRONTEND_URL}/")
        field = page.get_by_label("Tvoj kod", exact=False)
        expect(field).to_be_visible(timeout=SLOW_MS)

        # A wrong Code shows the generic error and stays on the Code screen.
        field.fill(wrong)
        page.get_by_role("button", name="Dalje").click()
        # (Next.js adds its own role=alert route announcer, so match the message text.)
        expect(page.get_by_text("Kod nije ispravan")).to_be_visible()
        expect(page).not_to_have_url(re.compile(r"/countdown/"))

        # A real Code leads to the Countdown with the greeting and the reveal button.
        field.fill(code)
        page.get_by_role("button", name="Dalje").click()
        expect(page).to_have_url(re.compile(r"/countdown/$"))
        expect(page.get_by_role("heading", level=1)).to_contain_text("Bok")
        open_button = page.get_by_role("button", name="Otvori svoj poklon")
        expect(open_button).to_be_visible()

        # "Open your gift": first-time message, and a <video> whose src is a blob: URL
        # (the clip is fetched with the bearer token and played from an object URL).
        open_button.click()
        expect(page).to_have_url(re.compile(r"/reveal/$"))
        expect(page.get_by_text("Ho ho ho")).to_be_visible()
        video = page.locator("video")
        expect(video).to_have_count(1)
        expect(video).to_have_attribute("src", re.compile(r"^blob:"))

        # Reloading spends no second "first time": the reminder message appears instead.
        page.reload()
        expect(page.get_by_text("Podsjetnik")).to_be_visible()
        expect(page.get_by_text("Ho ho ho")).to_have_count(0)
        expect(page.locator("video")).to_have_attribute("src", re.compile(r"^blob:"))
    except Exception:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(ARTIFACTS / "failure.png"), full_page=True)
        raise
