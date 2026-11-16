"""The `draw` command and `POST me/reveal` (in-process, real database, synthetic people)."""

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.cli import main
from app.db.models import Assignment, Event, Participant
from app.db.session import SessionLocal
from app.services import drawing
from app.services.draw import DrawImpossibleError
from app.services.drawing import run_draw
from tests.conftest import SYNTHETIC_NAMES, Seeded, seed_event

REVEAL = "/api/v1/me/reveal"


async def run_cli(*argv: str) -> int:
    # The CLI owns an event loop (asyncio.run), so it runs in a worker thread.
    return await asyncio.to_thread(main, list(argv))


async def link_partners(seeded: Seeded, *pairs: tuple[str, str]) -> None:
    async with SessionLocal() as s:
        for a, b in pairs:
            ia, ib = seeded.participant_ids[a], seeded.participant_ids[b]
            await s.execute(update(Participant).where(Participant.id == ia).values(partner_id=ib))
            await s.execute(update(Participant).where(Participant.id == ib).values(partner_id=ia))
        await s.commit()


async def assignments(seeded: Seeded) -> dict[str, str]:
    """giver name -> receiver name, read straight from the database."""
    names = {v: k for k, v in seeded.participant_ids.items()}
    async with SessionLocal() as s:
        rows = (await s.scalars(select(Assignment))).all()
    return {names[a.giver_id]: names[a.receiver_id] for a in rows}


async def drawn_at() -> object:
    async with SessionLocal() as s:
        return await s.scalar(select(Event.drawn_at))


async def login(client: httpx.AsyncClient, code: str) -> dict[str, str]:
    r = await client.post("/api/v1/session", json={"code": code})
    return {"Authorization": f"Bearer {r.json()['token']}"}


# --- CLI: draw -----------------------------------------------------------------------------


async def test_draw_prints_only_success_and_count(
    seeded: Seeded, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run_cli("draw") == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "Draw complete: 4 participants."
    # No name, id or pair anywhere in stdout or stderr (which also carries the log lines).
    everything = captured.out + captured.err
    for name in SYNTHETIC_NAMES:
        assert name not in everything
    for pid in seeded.participant_ids.values():
        assert str(pid) not in everything
    assert await drawn_at() is not None
    result = await assignments(seeded)
    assert sorted(result) == sorted(result.values()) == sorted(SYNTHETIC_NAMES)
    assert all(g != r for g, r in result.items())


async def test_draw_respects_partners_in_both_directions(seeded: Seeded) -> None:
    await link_partners(seeded, ("Person A", "Person B"), ("Person C", "Person D"))
    for _ in range(25):
        assert await run_cli("draw", "--redraw") == 0
        result = await assignments(seeded)
        for a, b in (("Person A", "Person B"), ("Person C", "Person D")):
            assert result[a] != b
            assert result[b] != a


async def test_second_draw_is_refused_without_redraw(
    seeded: Seeded, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run_cli("draw") == 0
    before = await assignments(seeded)
    first_drawn_at = await drawn_at()
    capsys.readouterr()

    assert await run_cli("draw") == 1
    captured = capsys.readouterr()
    assert "--redraw" in captured.err
    assert captured.out == ""
    assert await assignments(seeded) == before
    assert await drawn_at() == first_drawn_at


async def test_redraw_replaces_assignments_and_resets_first_reveal(
    client: httpx.AsyncClient, seeded: Seeded
) -> None:
    assert await run_cli("draw") == 0
    headers = await login(client, seeded.codes["Person A"])
    assert (await client.post(REVEAL, headers=headers)).json()["first_time"] is True

    changed = False
    for _ in range(30):  # a redraw can repeat the same draw by chance; 4 people have few
        before = await assignments(seeded)
        assert await run_cli("draw", "--redraw") == 0
        after = await assignments(seeded)
        assert sorted(after) == sorted(after.values()) == sorted(SYNTHETIC_NAMES)
        changed = changed or after != before
    assert changed
    async with SessionLocal() as s:
        assert await s.scalar(select(func.count()).select_from(Assignment)) == 4
    # A new receiver deserves a new first-time message.
    assert (await client.post(REVEAL, headers=headers)).json()["first_time"] is True


async def test_redraw_without_a_prior_draw_just_draws(seeded: Seeded) -> None:
    assert await run_cli("draw", "--redraw") == 0
    assert len(await assignments(seeded)) == 4


async def test_impossible_draw_writes_nothing_and_exits_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Three people where A and B are Partners: both would have to draw C.
    seeded = await seed_event(("Person A", "Person B", "Person C"))
    await link_partners(seeded, ("Person A", "Person B"))
    assert await run_cli("draw") == 1
    captured = capsys.readouterr()
    assert "no valid draw" in captured.err
    assert "nothing was written" in captured.err
    assert captured.out == ""
    assert await assignments(seeded) == {}
    assert await drawn_at() is None


async def test_impossible_redraw_keeps_the_old_draw(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run_cli("draw") == 0
    before = await assignments(seeded)
    first_drawn_at = await drawn_at()
    capsys.readouterr()

    def impossible(*_args: object, **_kwargs: object) -> dict[uuid.UUID, uuid.UUID]:
        raise DrawImpossibleError

    monkeypatch.setattr(drawing, "draw", impossible)
    assert await run_cli("draw", "--redraw") == 1
    assert "no valid draw" in capsys.readouterr().err
    assert await assignments(seeded) == before
    assert await drawn_at() == first_drawn_at


async def test_redraw_failure_midway_rolls_back_the_old_draw(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert await run_cli("draw") == 0
    before = await assignments(seeded)
    first_drawn_at = await drawn_at()
    someone = next(iter(seeded.participant_ids.values()))

    def self_draw(*_args: object, **_kwargs: object) -> dict[uuid.UUID, uuid.UUID]:
        # Violates the not-self CHECK, after the old rows were already deleted in the transaction.
        return {someone: someone}

    monkeypatch.setattr(drawing, "draw", self_draw)
    async with SessionLocal() as s:
        with pytest.raises(IntegrityError):
            await run_draw(s, redraw=True)
    assert await assignments(seeded) == before
    assert await drawn_at() == first_drawn_at


async def test_draw_without_an_event_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert await run_cli("draw") == 1
    assert "no event" in capsys.readouterr().err


# --- API: POST me/reveal -------------------------------------------------------------------


async def test_reveal_requires_a_session(client: httpx.AsyncClient, seeded: Seeded) -> None:
    assert (await client.post(REVEAL)).status_code == 401
    r = await client.post(REVEAL, headers={"Authorization": "Bearer nonsense"})
    assert r.status_code == 401


async def test_reveal_before_the_draw_is_404_and_changes_nothing(
    client: httpx.AsyncClient, seeded: Seeded
) -> None:
    headers = await login(client, seeded.codes["Person A"])
    assert (await client.post(REVEAL, headers=headers)).status_code == 404
    me = (await client.get("/api/v1/me", headers=headers)).json()
    assert me["drawn"] is False
    assert me["has_revealed"] is False


async def test_each_participant_learns_only_their_own_receiver(
    client: httpx.AsyncClient, seeded: Seeded
) -> None:
    assert await run_cli("draw") == 0
    expected = await assignments(seeded)
    received: list[str] = []
    for name in SYNTHETIC_NAMES:
        headers = await login(client, seeded.codes[name])
        r = await client.post(REVEAL, headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"receiver_display_name", "receiver_accusative_name", "first_time"}
        assert body["receiver_display_name"] == expected[name]
        assert body["receiver_accusative_name"] == expected[name]  # seed uses name for each form
        assert body["first_time"] is True
        assert r.headers["cache-control"] == "private, no-store"
        # Nothing about anyone else's receiver leaks into this response.
        others = [v for k, v in expected.items() if k != name and v != expected[name]]
        assert not any(o in r.text for o in others)
        received.append(body["receiver_display_name"])
    assert sorted(received) == sorted(SYNTHETIC_NAMES)  # each person is someone's receiver once


async def test_reveal_takes_no_input_to_choose_whose_receiver(
    client: httpx.AsyncClient, seeded: Seeded
) -> None:
    assert await run_cli("draw") == 0
    expected = await assignments(seeded)
    headers = await login(client, seeded.codes["Person A"])
    other = seeded.participant_ids["Person B"]
    r = await client.post(
        REVEAL, headers=headers, params={"participant_id": str(other)}, json={"giver": str(other)}
    )
    assert r.json()["receiver_display_name"] == expected["Person A"]


async def test_first_time_is_true_exactly_once(client: httpx.AsyncClient, seeded: Seeded) -> None:
    assert await run_cli("draw") == 0
    headers = await login(client, seeded.codes["Person A"])
    assert (await client.get("/api/v1/me", headers=headers)).json()["has_revealed"] is False
    results = [(await client.post(REVEAL, headers=headers)).json() for _ in range(3)]
    assert [r["first_time"] for r in results] == [True, False, False]
    assert len({r["receiver_display_name"] for r in results}) == 1
    assert (await client.get("/api/v1/me", headers=headers)).json()["has_revealed"] is True
    # Another participant's first reveal is unaffected.
    other = await login(client, seeded.codes["Person B"])
    assert (await client.post(REVEAL, headers=other)).json()["first_time"] is True


async def test_first_time_is_true_once_under_concurrent_calls(
    client: httpx.AsyncClient, seeded: Seeded
) -> None:
    assert await run_cli("draw") == 0
    headers = await login(client, seeded.codes["Person A"])
    responses = await asyncio.gather(*(client.post(REVEAL, headers=headers) for _ in range(12)))
    assert all(r.status_code == 200 for r in responses)
    assert sum(r.json()["first_time"] for r in responses) == 1
