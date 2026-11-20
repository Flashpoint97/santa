"""Two independent events side by side (in-process CLI, real database, synthetic people)."""

import asyncio
import io
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.cli import main
from app.db.models import Animation, Assignment, Event, Participant, RateLimitHit, Session
from app.db.session import SessionLocal
from tests.conftest import code_hash
from tests.test_cli_load import valid_list, write
from tests.test_session_me import bearer, login

EVENT_A = "Synthetic Exchange"
EVENT_B = "Druga grupa Čačak"
# The same display names in both events: names are unique per event, not globally.
NAMES = ("Osoba A", "Osoba B", "Osoba C", "Osoba D")


async def run_cli(*argv: str) -> int:
    # The CLI owns an event loop (asyncio.run), so it runs in a worker thread.
    return await asyncio.to_thread(main, list(argv))


async def load_circle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], event_name: str
) -> dict[str, str]:
    """Load a synthetic list under `event_name`; return display name -> printed Code."""
    data = valid_list()
    data["event"]["name"] = event_name
    folder = tmp_path / event_name.casefold().replace(" ", "-")
    folder.mkdir()
    capsys.readouterr()
    assert await run_cli("load", str(write(folder, data))) == 0
    out = capsys.readouterr().out
    assert event_name in out
    codes = {
        line.split(":")[0].strip(): line.split(":")[1].strip()
        for line in out.splitlines()
        if line.startswith("  ")
    }
    assert set(codes) == set(NAMES)
    return codes


async def two_circles(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> tuple[dict[str, str], dict[str, str]]:
    return (
        await load_circle(tmp_path, capsys, EVENT_A),
        await load_circle(tmp_path, capsys, EVENT_B),
    )


async def event_id(name: str) -> object:
    async with SessionLocal() as s:
        return await s.scalar(select(Event.id).where(Event.name == name))


async def assignments_of(name: str) -> set[tuple[object, object]]:
    async with SessionLocal() as s:
        rows = await s.execute(
            select(Assignment.giver_id, Assignment.receiver_id).where(
                Assignment.event_id == await event_id(name)
            )
        )
        return {(g, r) for g, r in rows}


async def count(model: type, **where: object) -> int:
    async with SessionLocal() as s:
        stmt = select(func.count()).select_from(model)
        for column, value in where.items():
            stmt = stmt.where(getattr(model, column) == value)
        return await s.scalar(stmt) or 0


async def stored_hash(event: str, name: str) -> str:
    async with SessionLocal() as s:
        value = await s.scalar(
            select(Participant.code_hash).where(
                Participant.display_name == name,
                Participant.event_id == await event_id(event),
            )
        )
    assert value is not None
    return value


def type_into_stdin(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(text))


# ---------------------------------------------------------------- load


async def test_a_second_event_loads_next_to_the_first(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    codes_a, codes_b = await two_circles(tmp_path, capsys)
    assert await count(Event) == 2
    assert await count(Participant) == 8
    assert await count(Animation) == 8
    assert not set(codes_a.values()) & set(codes_b.values())  # all eight Codes differ


async def test_same_name_is_refused_casefold_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await load_circle(tmp_path, capsys, EVENT_B)  # non-ASCII name: casefold, not lower()
    data = valid_list()
    data["event"]["name"] = "  druga GRUPA čačak "
    folder = tmp_path / "again"
    folder.mkdir()
    capsys.readouterr()
    assert await run_cli("load", str(write(folder, data))) == 1
    captured = capsys.readouterr()
    assert "already exists" in captured.err
    assert not any(ch.isdigit() for ch in captured.out)  # no Codes on refusal
    assert await count(Event) == 1
    assert await count(Participant) == 4


async def test_a_new_event_with_a_different_name_is_not_blocked_by_a_drawn_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await load_circle(tmp_path, capsys, EVENT_A)
    assert await run_cli("draw") == 0
    await load_circle(tmp_path, capsys, EVENT_B)
    assert await count(Event) == 2


async def test_load_avoids_codes_used_by_another_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    codes_a = await load_circle(tmp_path, capsys, EVENT_A)
    clash = codes_a["Osoba A"]
    fresh = iter(["100001", "100002", "100003", "100004"])
    drawn = iter([clash, clash, *fresh])  # a clash with event A's Code, twice, then free ones
    monkeypatch.setattr("app.services.security.generate_login_code", lambda: next(drawn))
    codes_b = await load_circle(tmp_path, capsys, EVENT_B)  # no IntegrityError
    assert clash not in codes_b.values()
    assert sorted(codes_b.values()) == ["100001", "100002", "100003", "100004"]
    assert await count(Participant) == 8


# ---------------------------------------------------------------- --event resolution


@pytest.mark.parametrize(
    "argv",
    [
        ("draw",),
        ("draw", "--redraw"),
        ("status",),
        ("new-code", "Osoba A"),
        ("wipe",),
    ],
)
async def test_omitted_event_with_two_events_is_refused_and_lists_names(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    argv: tuple[str, ...],
) -> None:
    codes_a, _ = await two_circles(tmp_path, capsys)
    type_into_stdin(monkeypatch, "OBRIŠI\n")
    capsys.readouterr()
    assert await run_cli(*argv) == 1
    captured = capsys.readouterr()
    assert EVENT_A in captured.err
    assert EVENT_B in captured.err
    assert "--event" in captured.err
    assert captured.out == ""
    assert "Osoba" not in captured.err  # event names only, no participants
    # Nothing changed anywhere.
    assert await count(Event) == 2
    assert await count(Participant) == 8
    assert await count(Assignment) == 0
    assert await stored_hash(EVENT_A, "Osoba A") == code_hash(codes_a["Osoba A"])


@pytest.mark.parametrize("argv", [("draw",), ("status",), ("new-code", "Osoba A"), ("wipe",)])
async def test_unknown_event_name_is_refused_and_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    argv: tuple[str, ...],
) -> None:
    await two_circles(tmp_path, capsys)
    type_into_stdin(monkeypatch, "OBRIŠI\n")
    capsys.readouterr()
    assert await run_cli(*argv, "--event", "No Such Circle") == 1
    captured = capsys.readouterr()
    assert "no event with that name" in captured.err
    assert captured.out == ""
    assert await count(Event) == 2
    assert await count(Participant) == 8
    assert await count(Assignment) == 0


async def test_a_single_event_needs_no_event_option(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await load_circle(tmp_path, capsys, EVENT_A)
    assert await run_cli("draw") == 0
    assert await run_cli("status") == 0
    assert await run_cli("new-code", "Osoba A") == 0


async def test_event_name_matches_casefold_with_non_ascii_letters(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await two_circles(tmp_path, capsys)
    capsys.readouterr()
    assert await run_cli("draw", "--event", "DRUGA grupa ČAČAK") == 0
    assert "Draw complete: 4 participants." in capsys.readouterr().out
    assert len(await assignments_of(EVENT_B)) == 4


# ---------------------------------------------------------------- scoping


async def test_draw_of_b_leaves_a_untouched_and_redraw_only_replaces_its_own(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await two_circles(tmp_path, capsys)
    assert await run_cli("draw", "--event", EVENT_A) == 0
    a_before = await assignments_of(EVENT_A)
    assert len(a_before) == 4
    assert await assignments_of(EVENT_B) == set()

    assert await run_cli("draw", "--event", EVENT_B) == 0
    assert await assignments_of(EVENT_A) == a_before
    b_before = await assignments_of(EVENT_B)
    assert len(b_before) == 4
    async with SessionLocal() as s:
        drawn = dict((await s.execute(select(Event.name, Event.drawn_at))).all())
    assert drawn[EVENT_A] is not None
    assert drawn[EVENT_B] is not None

    # Each event has its own "already drawn" state, and a re-draw of B never touches A.
    capsys.readouterr()
    assert await run_cli("draw", "--event", EVENT_B) == 1
    assert "already happened" in capsys.readouterr().err
    assert await run_cli("draw", "--event", EVENT_B, "--redraw") == 0
    assert await assignments_of(EVENT_A) == a_before
    assert len(await assignments_of(EVENT_B)) == 4


async def test_each_event_draws_only_among_its_own_participants(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await two_circles(tmp_path, capsys)
    assert await run_cli("draw", "--event", EVENT_B) == 0
    async with SessionLocal() as s:
        people = {p.id: p.event_id for p in (await s.scalars(select(Participant))).all()}
        rows = (await s.scalars(select(Assignment))).all()
    b = await event_id(EVENT_B)
    assert len(rows) == 4
    for a in rows:
        assert people[a.giver_id] == people[a.receiver_id] == a.event_id == b


async def test_status_lists_only_the_chosen_events_participants(
    client: httpx.AsyncClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    codes_a, _ = await two_circles(tmp_path, capsys)
    await login(client, codes_a["Osoba B"])
    capsys.readouterr()
    assert await run_cli("status", "--event", EVENT_A) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Osoba A: ne",
        "Osoba B: da",
        "Osoba C: ne",
        "Osoba D: ne",
    ]
    assert await run_cli("status", "--event", EVENT_B) == 0
    assert capsys.readouterr().out.splitlines() == [f"{n}: ne" for n in NAMES]


async def test_new_code_finds_the_name_only_in_the_chosen_event(
    client: httpx.AsyncClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    codes_a, codes_b = await two_circles(tmp_path, capsys)
    token_a = await login(client, codes_a["Osoba A"])
    token_b = await login(client, codes_b["Osoba A"])  # same name, other event
    hash_a = await stored_hash(EVENT_A, "Osoba A")

    capsys.readouterr()
    assert await run_cli("new-code", "osoba a", "--event", EVENT_B.upper()) == 0
    new = capsys.readouterr().out.strip().rsplit(":", 1)[1].strip()
    assert await stored_hash(EVENT_B, "Osoba A") == code_hash(new)
    assert await stored_hash(EVENT_A, "Osoba A") == hash_a  # event A's namesake is untouched
    assert (await client.get("/api/v1/me", headers=bearer(token_a))).status_code == 200
    assert (await client.get("/api/v1/me", headers=bearer(token_b))).status_code == 401
    assert (
        await client.post("/api/v1/session", json={"code": codes_b["Osoba A"]})
    ).status_code == 400
    assert (
        await client.post("/api/v1/session", json={"code": codes_a["Osoba A"]})
    ).status_code == 200

    # A name that exists only in the other event is "unknown" here.
    assert await run_cli("new-code", "Osoba Z", "--event", EVENT_A) == 1
    assert "no participant with that name" in capsys.readouterr().err


async def test_new_code_avoids_codes_of_other_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    codes_a, _ = await two_circles(tmp_path, capsys)
    drawn = iter([codes_a["Osoba C"], "424242"])  # event A's Code, then a free one
    monkeypatch.setattr("app.services.security.generate_login_code", lambda: next(drawn))
    capsys.readouterr()
    assert await run_cli("new-code", "Osoba A", "--event", EVENT_B) == 0
    assert capsys.readouterr().out.strip().endswith("424242")


async def test_wipe_of_b_leaves_a_and_its_codes_working(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    codes_a, codes_b = await two_circles(tmp_path, capsys)
    assert await run_cli("draw", "--event", EVENT_A) == 0
    assert await run_cli("draw", "--event", EVENT_B) == 0
    a_before = await assignments_of(EVENT_A)
    token_a = await login(client, codes_a["Osoba A"])
    await login(client, codes_b["Osoba A"])
    assert (await client.post("/api/v1/session", json={"code": "000000"})).status_code == 400

    type_into_stdin(monkeypatch, "OBRIŠI\n")
    capsys.readouterr()
    assert await run_cli("wipe", "--event", EVENT_B.lower()) == 0
    captured = capsys.readouterr()
    # The prompt names the event and its participant count; no participant names.
    assert EVENT_B in captured.err
    assert "4 participants" in captured.err
    assert "Osoba" not in captured.out + captured.err

    assert await count(Event) == 1
    assert await count(Event, name=EVENT_B) == 0
    assert await count(Participant) == 4
    assert await count(Animation) == 4
    assert await count(Session) == 1  # only A's Session is left
    assert await assignments_of(EVENT_A) == a_before
    assert await count(RateLimitHit) == 0

    # A's existing token and A's Code still work; B's Codes are gone.
    assert (await client.get("/api/v1/me", headers=bearer(token_a))).status_code == 200
    assert (
        await client.post("/api/v1/session", json={"code": codes_a["Osoba B"]})
    ).status_code == 200
    assert (
        await client.post("/api/v1/session", json={"code": codes_b["Osoba B"]})
    ).status_code == 400

    # With one event left the option is optional again.
    assert await run_cli("status") == 0


async def test_wipe_with_wrong_confirmation_deletes_nothing_in_either_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await two_circles(tmp_path, capsys)
    type_into_stdin(monkeypatch, "no\n")
    capsys.readouterr()
    assert await run_cli("wipe", "--event", EVENT_A) == 1
    assert "nothing was deleted" in capsys.readouterr().err
    assert await count(Event) == 2
    assert await count(Participant) == 8
