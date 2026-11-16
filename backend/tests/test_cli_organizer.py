"""`new-code`, `status` and `wipe` (in-process, real database, synthetic people)."""

import asyncio
import io

import httpx
import pytest
from sqlalchemy import func, select

from app.cli import main
from app.db.models import (
    Animation,
    Assignment,
    Base,
    Event,
    Participant,
    Session,
)
from app.db.session import SessionLocal
from app.services.drawing import run_draw
from tests.conftest import Seeded, code_hash, seed_event
from tests.test_session_me import bearer, login

NAMES = ("Osoba B", "Osoba A", "Osoba Č", "Osoba C")


async def run_cli(*argv: str) -> int:
    # The CLI owns an event loop (asyncio.run), so it runs in a worker thread.
    return await asyncio.to_thread(main, list(argv))


async def count(model: type) -> int:
    async with SessionLocal() as s:
        return await s.scalar(select(func.count()).select_from(model)) or 0


def printed_code(out: str) -> str:
    code = out.strip().rsplit(":", 1)[1].strip()
    assert len(code) == 6
    assert code.isdigit()
    return code


async def stored_hash(name: str) -> str:
    async with SessionLocal() as s:
        value = await s.scalar(
            select(Participant.code_hash).where(Participant.display_name == name)
        )
    assert value is not None
    return value


# ---------------------------------------------------------------- new-code


async def test_new_code_replaces_code_and_ends_sessions(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    seeded = await seed_event(NAMES)
    old = seeded.codes["Osoba A"]
    token_1 = await login(client, old)
    token_2 = await login(client, old)  # two Sessions on two devices
    other_token = await login(client, seeded.codes["Osoba B"])
    other_hash = await stored_hash("Osoba B")
    assert await count(Session) == 3

    # Case-insensitive match.
    assert await run_cli("new-code", "osoba a") == 0
    new = printed_code(capsys.readouterr().out)
    assert new != old

    assert await stored_hash("Osoba A") == code_hash(new)  # only the keyed hash is stored
    assert await stored_hash("Osoba A") != new
    assert await stored_hash("Osoba B") == other_hash  # others untouched

    # The old Code fails immediately, the new one works, the old Sessions are 401.
    assert (await client.post("/api/v1/session", json={"code": old})).status_code == 400
    for token in (token_1, token_2):
        assert (await client.get("/api/v1/me", headers=bearer(token))).status_code == 401
    assert (await client.get("/api/v1/me", headers=bearer(other_token))).status_code == 200
    assert await count(Session) == 1  # only the other Participant's Session is left
    new_token = await login(client, new)
    me = (await client.get("/api/v1/me", headers=bearer(new_token))).json()
    assert me["display_name"] == "Osoba A"


async def test_new_code_matches_non_ascii_names_case_insensitively() -> None:
    await seed_event(NAMES)
    before = await stored_hash("Osoba Č")
    assert await run_cli("new-code", "  osoba č ") == 0
    assert await stored_hash("Osoba Č") != before


async def test_new_code_unknown_name_exits_nonzero_and_changes_nothing(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    seeded = await seed_event(NAMES)
    token = await login(client, seeded.codes["Osoba A"])
    before = {n: await stored_hash(n) for n in NAMES}
    capsys.readouterr()  # setup logs are not the CLI's output
    assert await run_cli("new-code", "Nobody") == 1
    captured = capsys.readouterr()
    assert "no participant with that name" in captured.err
    assert captured.out == ""
    assert {n: await stored_hash(n) for n in NAMES} == before
    assert (await client.get("/api/v1/me", headers=bearer(token))).status_code == 200


async def test_new_code_without_event_exits_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    assert await run_cli("new-code", "Osoba A") == 1
    captured = capsys.readouterr()
    assert "no event is loaded" in captured.err
    assert captured.out == ""


async def test_new_code_avoids_codes_already_in_use(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seeded = await seed_event(NAMES)
    # Another Participant's Code, the Participant's own old Code, then a free one.
    drawn = iter([seeded.codes["Osoba B"], seeded.codes["Osoba A"], "424242"])
    monkeypatch.setattr("app.services.security.generate_login_code", lambda: next(drawn))
    assert await run_cli("new-code", "Osoba A") == 0
    assert printed_code(capsys.readouterr().out) == "424242"


async def test_new_code_logs_no_code_and_no_name(capsys: pytest.CaptureFixture[str]) -> None:
    seeded = await seed_event(NAMES)
    assert await run_cli("new-code", "Osoba A") == 0
    captured = capsys.readouterr()
    code = printed_code(captured.out)
    assert code not in captured.err
    assert seeded.codes["Osoba A"] not in captured.err
    assert "Osoba" not in captured.err
    assert "participant_code_replaced" in captured.err  # it did log, just not anything private
    assert captured.out.count(code) == 1  # printed once


# ---------------------------------------------------------------- status


async def test_status_lists_each_participant_sorted_with_login_state(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    seeded = await seed_event(NAMES)
    await login(client, seeded.codes["Osoba B"])
    capsys.readouterr()  # setup logs are not the CLI's output
    assert await run_cli("status") == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "Osoba A: ne",
        "Osoba B: da",
        "Osoba C: ne",
        "Osoba Č: ne",
    ]
    assert captured.err == ""


async def test_status_shows_nothing_about_assignments_reveals_or_codes(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    seeded = await seed_event(NAMES)
    async with SessionLocal() as s:
        await run_draw(s, redraw=False)
    token = await login(client, seeded.codes["Osoba A"])
    assert (await client.post("/api/v1/me/reveal", headers=bearer(token))).status_code == 200
    capsys.readouterr()

    assert await run_cli("status") == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert len(lines) == 4
    for line, name in zip(lines, sorted(NAMES, key=str.casefold), strict=True):
        # Exactly "<name>: da|ne": no third field, no other person's name on the line.
        assert line.startswith(f"{name}: ")
        assert line.removeprefix(f"{name}: ") in {"da", "ne"}
    for code in seeded.codes.values():
        assert code not in captured.out + captured.err


async def test_status_without_event_exits_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    assert await run_cli("status") == 1
    captured = capsys.readouterr()
    assert "no event is loaded" in captured.err
    assert captured.out == ""


# ---------------------------------------------------------------- wipe


async def seed_everything(client: httpx.AsyncClient) -> Seeded:
    """An event with a draw, Sessions, an Animation and rate-limit hits."""
    seeded = await seed_event(NAMES)
    async with SessionLocal() as s:
        await run_draw(s, redraw=False)
    await login(client, seeded.codes["Osoba A"])
    for _ in range(2):  # failed logins record rate-limit hits
        assert (await client.post("/api/v1/session", json={"code": "000000"})).status_code == 400
    async with SessionLocal() as s:
        s.add(
            Animation(
                participant_id=seeded.participant_ids["Osoba A"],
                video=b"x",
                content_type="video/mp4",
                byte_size=1,
            )
        )
        await s.commit()
    return seeded


async def total_rows() -> dict[str, int]:
    return {t.name: await count(t) for t in Base.metadata.sorted_tables}


def type_into_stdin(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(text))


async def test_wipe_with_confirmation_leaves_no_rows_anywhere(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    await seed_everything(client)
    capsys.readouterr()  # setup logs (default structlog goes to stdout) are not the CLI's output
    before = await total_rows()
    for table in ("events", "participants", "sessions", "assignments", "animations"):
        assert before[table] > 0, table
    assert before["rate_limit_hits"] > 0

    type_into_stdin(monkeypatch, "OBRIŠI\n")
    assert await run_cli("wipe") == 0
    captured = capsys.readouterr()
    assert "deleted" in captured.out
    assert await total_rows() == {t.name: 0 for t in Base.metadata.sorted_tables}
    assert "Osoba" not in captured.out + captured.err  # nothing private echoed


@pytest.mark.parametrize("typed", ["", "\n", "ne\n", "obriši\n", "OBRISI\n", "OBRIŠI please\n"])
async def test_wipe_with_wrong_confirmation_deletes_nothing(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    typed: str,
) -> None:
    await seed_everything(client)
    capsys.readouterr()  # setup logs are not the CLI's output
    before = await total_rows()
    type_into_stdin(monkeypatch, typed)
    assert await run_cli("wipe") == 1
    captured = capsys.readouterr()
    assert "nothing was deleted" in captured.err
    assert captured.out == ""
    assert await total_rows() == before
    assert await count(Event) == 1
    assert await count(Assignment) == len(NAMES)


async def test_wipe_without_event_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    type_into_stdin(monkeypatch, "OBRIŠI\n")
    assert await run_cli("wipe") == 1
    assert "no event is loaded" in capsys.readouterr().err
