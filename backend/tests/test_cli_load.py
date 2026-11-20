"""List-file validation and the `load` command (in-process; synthetic data only)."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import undefer

from app.cli import main
from app.db.models import Animation, Event, Participant, Session
from app.db.session import SessionLocal
from app.services.list_file import ListFileError, parse_list_file
from tests.conftest import code_hash
from tests.gifs import make_gif

EXAMPLE = Path(__file__).resolve().parent.parent / "list.example.json"


def valid_list() -> dict[str, Any]:
    def person(name: str, partner: str | None = None) -> dict[str, Any]:
        return {
            "display_name": name,
            "vocative_name": name,
            "accusative_name": name,
            "partner": partner,
            "note": f"Note {name}",
            "gif": f"{name.lower().replace(' ', '-')}.gif",
        }

    return {
        "event": {
            "name": "Synthetic Exchange",
            "budget_amount": "25.50",
            "budget_currency": "EUR",
            "exchange_at": "2026-12-25T12:00",
            "place": "Nowhere 1",
            "after_event_message": "Synthetic birthday.",
        },
        "participants": [
            person("Osoba A", "Osoba B"),
            person("Osoba B", "Osoba A"),
            person("Osoba C"),
            person("Osoba D"),
        ],
    }


def problems_of(data: dict[str, Any]) -> list[str]:
    with pytest.raises(ListFileError) as exc:
        parse_list_file(json.dumps(data))
    return exc.value.problems


def test_valid_list_parses_and_converts_zagreb_time_to_utc() -> None:
    parsed = parse_list_file(json.dumps(valid_list()))
    assert parsed.event.exchange_at_utc.isoformat() == "2026-12-25T11:00:00+00:00"
    # Summer time is UTC+2.
    data = valid_list()
    data["event"]["exchange_at"] = "2026-07-01T12:00"
    assert parse_list_file(json.dumps(data)).event.exchange_at_utc.hour == 10


def test_placeholder_example_file_is_valid() -> None:
    parsed = parse_list_file(EXAMPLE.read_bytes())
    assert len(parsed.participants) == 8


def test_not_json() -> None:
    with pytest.raises(ListFileError):
        parse_list_file("{nope")


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda d: d["participants"][1].update(display_name="osoba a"), "duplicate"),
        (lambda d: d["participants"][0].update(partner="Nobody"), "no participant"),
        (lambda d: d["participants"][0].update(partner="Osoba A"), "themselves"),
        (lambda d: d["participants"][2].update(partner="Osoba A"), "symmetric"),
        (lambda d: d["participants"][1].update(partner=None), "symmetric"),
        (lambda d: d["event"].update(exchange_at="2026-12-25T12:00+01:00"), "offset"),
        (lambda d: d["event"].update(exchange_at="2026-12-25T12:00Z"), "offset"),
        (lambda d: d["event"].update(exchange_at="not a time"), "exchange_at"),
        (lambda d: d["event"].update(exchange_at="2026-03-29T02:30"), "does not exist"),
        (lambda d: d["event"].update(budget_amount="-1"), "budget_amount"),
        (lambda d: d["event"].update(budget_currency="euro"), "budget_currency"),
        (lambda d: d["event"].pop("place"), "place"),
        (lambda d: d["event"].update(surprise=1), "surprise"),
        (lambda d: d["participants"][0].pop("accusative_name"), "accusative_name"),
        (lambda d: d.update(participants=d["participants"][:1]), "participants"),
    ],
)
def test_invalid_lists_are_rejected(mutate: Any, needle: str) -> None:
    data = valid_list()
    mutate(data)
    assert any(needle in p for p in problems_of(data))


def test_errors_do_not_echo_values() -> None:
    data = valid_list()
    data["participants"][0]["note"] = 12345  # wrong type
    data["participants"][0]["display_name"] = ""
    text = " ".join(problems_of(data))
    assert "12345" not in text


def write(tmp_path: Path, data: dict[str, Any]) -> Path:
    """Write the list file plus a tiny GIF for each participant that names one."""
    for person in data["participants"]:
        gif = tmp_path / person.get("gif", "")
        if person.get("gif") and not gif.exists():
            gif.parent.mkdir(parents=True, exist_ok=True)
            gif.write_bytes(make_gif())
    path = tmp_path / "list.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


async def run_cli(*argv: str) -> int:
    # The CLI owns an event loop (asyncio.run), so it runs in a worker thread.
    return await asyncio.to_thread(main, list(argv))


async def count(model: type) -> int:
    async with SessionLocal() as s:
        return await s.scalar(select(func.count()).select_from(model)) or 0


async def test_load_creates_everything_and_prints_codes_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run_cli("load", str(write(tmp_path, valid_list()))) == 0
    out = capsys.readouterr().out
    printed = {
        line.split(":")[0].strip(): line.split(":")[1].strip()
        for line in out.splitlines()
        if line.startswith("  ")
    }
    assert set(printed) == {"Osoba A", "Osoba B", "Osoba C", "Osoba D"}
    codes = list(printed.values())
    assert all(len(c) == 6 and c.isdigit() for c in codes)
    assert len(set(codes)) == 4  # unique within the event

    async with SessionLocal() as s:
        event = (await s.scalars(select(Event))).one()
        assert event.exchange_at.isoformat() == "2026-12-25T11:00:00+00:00"
        assert str(event.budget_amount) == "25.50"
        assert event.drawn_at is None
        people = {p.display_name: p for p in (await s.scalars(select(Participant))).all()}
    assert len(people) == 4
    for name, code in printed.items():
        assert people[name].code_hash == code_hash(code)  # only the keyed HMAC is stored
        assert people[name].code_hash != code
        assert people[name].has_logged_in is False
    assert people["Osoba A"].partner_id == people["Osoba B"].id
    assert people["Osoba B"].partner_id == people["Osoba A"].id
    assert people["Osoba C"].partner_id is None
    assert people["Osoba A"].note == "Note Osoba A"


async def test_printed_code_logs_in(client: Any, tmp_path: Path, capsys: Any) -> None:
    assert await run_cli("load", str(write(tmp_path, valid_list()))) == 0
    out = capsys.readouterr().out
    code_c = next(
        line.split(":")[1].strip() for line in out.splitlines() if line.startswith("  Osoba C")
    )
    token = (await client.post("/api/v1/session", json={"code": code_c})).json()["token"]
    me = (await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})).json()
    assert me["display_name"] == "Osoba C"
    assert code_c not in json.dumps(me)


async def test_load_refuses_when_an_event_exists(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, valid_list())
    assert await run_cli("load", str(path)) == 0
    capsys.readouterr()
    assert await run_cli("load", str(path)) == 1
    captured = capsys.readouterr()
    assert "already exists" in captured.err
    assert not any(ch.isdigit() for ch in captured.out)  # no Codes on refusal
    assert await count(Event) == 1
    assert await count(Participant) == 4


async def test_invalid_list_exits_nonzero_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = valid_list()
    data["participants"][0]["partner"] = "Nobody"
    assert await run_cli("load", str(write(tmp_path, data))) == 1
    captured = capsys.readouterr()
    assert "nothing was written" in captured.err
    assert "no participant" in captured.err
    assert captured.out == ""
    assert await count(Event) == 0
    assert await count(Participant) == 0
    assert await count(Session) == 0


async def test_missing_file_exits_nonzero(tmp_path: Path) -> None:
    assert await run_cli("load", str(tmp_path / "nope.json")) == 1
    assert await count(Event) == 0


async def test_load_stores_one_mp4_animation_per_participant(tmp_path: Path) -> None:
    assert await run_cli("load", str(write(tmp_path, valid_list()))) == 0
    async with SessionLocal() as s:
        people = {p.id: p.display_name for p in (await s.scalars(select(Participant))).all()}
        rows = (await s.scalars(select(Animation).options(undefer(Animation.video)))).all()
    assert sorted(people[a.participant_id] for a in rows) == sorted(people.values())
    for a in rows:
        assert a.content_type == "video/mp4"
        assert a.video[4:8] == b"ftyp"
        assert a.byte_size == len(a.video) > 0


async def test_gif_paths_are_relative_to_the_list_files_directory(tmp_path: Path) -> None:
    data = valid_list()
    for person in data["participants"]:
        person["gif"] = f"media/{person['gif']}"
    assert await run_cli("load", str(write(tmp_path, data))) == 0
    assert await count(Animation) == 4


@pytest.mark.parametrize("kind", ["missing", "not-a-gif", "empty", "key-absent", "absolute"])
async def test_a_missing_or_invalid_gif_fails_validation_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    data = valid_list()
    path = write(tmp_path, data)
    target = tmp_path / data["participants"][2]["gif"]
    if kind == "missing":
        target.unlink()
    elif kind == "not-a-gif":
        target.write_bytes(b"%PDF-1.7 " + b"0" * 64)
    elif kind == "empty":
        target.write_bytes(b"")
    elif kind == "key-absent":
        del data["participants"][2]["gif"]
        path.write_text(json.dumps(data), encoding="utf-8")
    else:
        data["participants"][2]["gif"] = str(target)
        path.write_text(json.dumps(data), encoding="utf-8")
    assert await run_cli("load", str(path)) == 1
    captured = capsys.readouterr()
    assert "nothing was written" in captured.err
    # A key that is absent is reported by pydantic ("participants.2.gif"), the rest by us.
    assert ("participants.2.gif" if kind == "key-absent" else "participants[2].gif") in captured.err
    assert captured.out == ""
    assert await count(Event) == 0
    assert await count(Participant) == 0
    assert await count(Animation) == 0
