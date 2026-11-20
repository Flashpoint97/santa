"""Organizer CLI: `python -m app.cli <command>`. Runs against whatever DATABASE_URL points at.

Output never includes assignments. Codes are printed once, by `load`, and only their HMAC is stored.
`draw` prints only the participant count. `new-code` prints one Code, once; `status` and `wipe`
print no Codes and no Assignments.

One deployment can hold several independent events. `draw`, `new-code`, `status` and `wipe` take
`--event "<name>"`; it may be left out only while exactly one event exists.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.config import settings
from app.db.session import build_engine
from app.services.animation import prepare_animations
from app.services.drawing import AlreadyDrawnError, DrawImpossibleError, run_draw
from app.services.events import AmbiguousEventError, NoEventError, UnknownEventError
from app.services.list_file import ListFile, ListFileError, parse_list_file
from app.services.loader import EventExistsError, load_event
from app.services.organizer import (
    UnknownParticipantError,
    describe_event,
    participant_statuses,
    replace_code,
    wipe_event,
)

WIPE_CONFIRMATION = "OBRIŠI"


def _event_error(exc: Exception, *, outcome: str) -> None:
    """Report an event-resolution failure on stderr. Event names are fine in the organizer's
    terminal; Participants and Assignments never appear here."""
    if isinstance(exc, NoEventError):
        print(f"error: no event is loaded; {outcome}.", file=sys.stderr)
    elif isinstance(exc, UnknownEventError):
        print(f"error: no event with that name; {outcome}.", file=sys.stderr)
    elif isinstance(exc, AmbiguousEventError):
        print(
            f"error: several events exist; name one with --event; {outcome}. Events:",
            file=sys.stderr,
        )
        for name in exc.names:
            print(f"  - {name}", file=sys.stderr)
    else:  # pragma: no cover - callers only pass the three above
        raise exc


_EVENT_ERRORS = (NoEventError, UnknownEventError, AmbiguousEventError)


def _read_list(path: Path) -> tuple[ListFile, dict[str, bytes]] | None:
    """Read and validate the list file and convert its pictures; on any problem report it on
    stderr and return None. Nothing here touches the database."""
    try:
        parsed = parse_list_file(path.read_bytes())
        return parsed, prepare_animations(parsed, path.resolve().parent)
    except OSError as exc:
        print(f"error: cannot read {path}: {exc.strerror}", file=sys.stderr)
    except ListFileError as err:
        print(f"error: invalid list file {path}, nothing was written:", file=sys.stderr)
        for problem in err.problems:
            print(f"  - {problem}", file=sys.stderr)
    return None


async def _load(engine: AsyncEngine, parsed: ListFile, videos: dict[str, bytes]) -> int:
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        try:
            issued = await load_event(
                db, parsed, videos, secret=settings.code_hmac_secret.get_secret_value()
            )
        except EventExistsError:
            print(
                "error: an event with this name already exists; nothing was written.",
                file=sys.stderr,
            )
            return 1

    print(
        f"Loaded event {parsed.event.name!r} with {len(issued)} participants. "
        "Codes (shown only once):"
    )
    for item in issued:
        print(f"  {item.display_name}: {item.code}")
    return 0


async def _draw(engine: AsyncEngine, *, redraw: bool, event: str | None) -> int:
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        try:
            count = await run_draw(db, redraw=redraw, event_name=event)
        except _EVENT_ERRORS as exc:
            _event_error(exc, outcome="nothing was written")
        except AlreadyDrawnError:
            print(
                "error: the draw has already happened; nothing was written. "
                "Use --redraw to replace it.",
                file=sys.stderr,
            )
        except DrawImpossibleError:
            print(
                "error: no valid draw exists with these Partner links; nothing was written. "
                "Fix the list and try again.",
                file=sys.stderr,
            )
        else:
            # Only the outcome: never who drew whom.
            print(f"Draw complete: {count} participants.")
            return 0
    return 1


async def _new_code(engine: AsyncEngine, name: str, event: str | None) -> int:
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        try:
            code = await replace_code(
                db, secret=settings.code_hmac_secret.get_secret_value(), name=name, event_name=event
            )
        except _EVENT_ERRORS as exc:
            _event_error(exc, outcome="nothing was changed")
        except UnknownParticipantError:
            print("error: no participant with that name; nothing was changed.", file=sys.stderr)
        else:
            print(f"New code (shown only once): {code}")
            return 0
    return 1


async def _status(engine: AsyncEngine, event: str | None) -> int:
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        try:
            rows = await participant_statuses(db, event)
        except _EVENT_ERRORS as exc:
            _event_error(exc, outcome="nothing to show")
            return 1
    for row in rows:
        print(f"{row.display_name}: {'da' if row.logged_in else 'ne'}")
    return 0


async def _wipe(engine: AsyncEngine, event: str | None) -> int:
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        try:
            summary = await describe_event(db, event)
        except _EVENT_ERRORS as exc:
            _event_error(exc, outcome="nothing was deleted")
            return 1
        await db.rollback()  # end the read transaction: nothing is held open during the prompt
        print(
            f"This permanently deletes the event {summary.name!r} and all its data "
            f"({summary.participants} participants, Sessions, draw, animations). "
            f"Type {WIPE_CONFIRMATION} to confirm: ",
            end="",
            file=sys.stderr,
            flush=True,
        )
        if sys.stdin.readline().strip() != WIPE_CONFIRMATION:
            print("error: confirmation did not match; nothing was deleted.", file=sys.stderr)
            return 1
        try:
            await wipe_event(db, summary.name)  # the exact name that was confirmed
        except _EVENT_ERRORS:  # removed by someone else since the prompt
            print("error: that event no longer exists; nothing was deleted.", file=sys.stderr)
            return 1
    print(f"Event {summary.name!r} and all its data deleted.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("load", help="validate a list file, create an event and print Codes")
    load.add_argument("list_file", type=Path)
    event_help = "event name (case-insensitive); optional only while exactly one event exists"
    draw = sub.add_parser("draw", help="draw the Assignments (prints only the participant count)")
    draw.add_argument("--redraw", action="store_true", help="replace an existing draw, atomically")
    draw.add_argument("--event", help=event_help)
    new_code = sub.add_parser(
        "new-code", help="replace a Participant's Code and end their Sessions"
    )
    new_code.add_argument("name", help="display name (case-insensitive)")
    new_code.add_argument("--event", help=event_help)
    status = sub.add_parser("status", help="list who has logged in (nothing else)")
    status.add_argument("--event", help=event_help)
    wipe = sub.add_parser(
        "wipe", help="permanently delete one event and all its data (asks to confirm)"
    )
    wipe.add_argument("--event", help=event_help)
    return parser


async def _run(args: argparse.Namespace, prepared: tuple[ListFile, dict[str, bytes]] | None) -> int:
    # A fresh engine per invocation: the CLI is a short-lived process with its own event loop.
    engine = build_engine(settings.database_url)
    try:
        if args.command == "load":
            assert prepared is not None  # noqa: S101 - set by main() for this command
            return await _load(engine, *prepared)
        if args.command == "draw":
            return await _draw(engine, redraw=args.redraw, event=args.event)
        if args.command == "new-code":
            return await _new_code(engine, args.name, args.event)
        if args.command == "status":
            return await _status(engine, args.event)
        if args.command == "wipe":
            return await _wipe(engine, args.event)
        raise AssertionError(args.command)  # pragma: no cover - argparse enforces the choices
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Keep stdout for the command's own output (Codes); diagnostics go to stderr.
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    prepared: tuple[ListFile, dict[str, bytes]] | None = None
    if args.command == "load":
        # Validate the list and convert the pictures before touching the database.
        prepared = _read_list(args.list_file)
        if prepared is None:
            return 1
    return asyncio.run(_run(args, prepared))


if __name__ == "__main__":
    sys.exit(main())
