"""Organizer CLI: `python -m app.cli <command>`. Runs against whatever DATABASE_URL points at.

Output never includes assignments. Codes are printed once, by `load`, and only their HMAC is stored.
`draw` prints only the participant count. `new-code` prints one Code, once; `status` and `wipe`
print no Codes and no Assignments.
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
from app.services.drawing import AlreadyDrawnError, DrawImpossibleError, NoEventError, run_draw
from app.services.list_file import ListFile, ListFileError, parse_list_file
from app.services.loader import EventExistsError, load_event
from app.services.organizer import (
    UnknownParticipantError,
    count_participants,
    participant_statuses,
    replace_code,
    wipe_event,
)

WIPE_CONFIRMATION = "OBRIŠI"


def _read_list(path: Path) -> ListFile | None:
    """Read and validate the list file; report problems on stderr and return None."""
    try:
        return parse_list_file(path.read_bytes())
    except OSError as exc:
        print(f"error: cannot read {path}: {exc.strerror}", file=sys.stderr)
    except ListFileError as err:
        print(f"error: invalid list file {path}, nothing was written:", file=sys.stderr)
        for problem in err.problems:
            print(f"  - {problem}", file=sys.stderr)
    return None


async def _load(engine: AsyncEngine, parsed: ListFile) -> int:
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        try:
            issued = await load_event(
                db, parsed, secret=settings.code_hmac_secret.get_secret_value()
            )
        except EventExistsError:
            print("error: an event already exists; nothing was written.", file=sys.stderr)
            return 1

    print(f"Loaded event with {len(issued)} participants. Codes (shown only once):")
    for item in issued:
        print(f"  {item.display_name}: {item.code}")
    return 0


async def _draw(engine: AsyncEngine, *, redraw: bool) -> int:
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        try:
            count = await run_draw(db, redraw=redraw)
        except NoEventError:
            print("error: no event is loaded; nothing was written.", file=sys.stderr)
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


async def _new_code(engine: AsyncEngine, name: str) -> int:
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        try:
            code = await replace_code(
                db, secret=settings.code_hmac_secret.get_secret_value(), name=name
            )
        except NoEventError:
            print("error: no event is loaded; nothing was changed.", file=sys.stderr)
        except UnknownParticipantError:
            print("error: no participant with that name; nothing was changed.", file=sys.stderr)
        else:
            print(f"New code (shown only once): {code}")
            return 0
    return 1


async def _status(engine: AsyncEngine) -> int:
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        try:
            rows = await participant_statuses(db)
        except NoEventError:
            print("error: no event is loaded.", file=sys.stderr)
            return 1
    for row in rows:
        print(f"{row.display_name}: {'da' if row.logged_in else 'ne'}")
    return 0


async def _wipe(engine: AsyncEngine) -> int:
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        try:
            people = await count_participants(db)
        except NoEventError:
            print("error: no event is loaded; nothing was deleted.", file=sys.stderr)
            return 1
        await db.rollback()  # end the read transaction: nothing is held open during the prompt
        print(
            f"This permanently deletes the event and all its data ({people} participants, "
            f"Sessions, draw, animations). Type {WIPE_CONFIRMATION} to confirm: ",
            end="",
            file=sys.stderr,
            flush=True,
        )
        if sys.stdin.readline().strip() != WIPE_CONFIRMATION:
            print("error: confirmation did not match; nothing was deleted.", file=sys.stderr)
            return 1
        try:
            await wipe_event(db)
        except NoEventError:  # removed by someone else since the prompt
            print("error: no event is loaded; nothing was deleted.", file=sys.stderr)
            return 1
    print("Event and all its data deleted.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("load", help="validate a list file, create the event and print Codes")
    load.add_argument("list_file", type=Path)
    draw = sub.add_parser("draw", help="draw the Assignments (prints only the participant count)")
    draw.add_argument("--redraw", action="store_true", help="replace an existing draw, atomically")
    new_code = sub.add_parser(
        "new-code", help="replace a Participant's Code and end their Sessions"
    )
    new_code.add_argument("name", help="display name (case-insensitive)")
    sub.add_parser("status", help="list who has logged in (nothing else)")
    sub.add_parser("wipe", help="permanently delete the event and all its data (asks to confirm)")
    return parser


async def _run(args: argparse.Namespace, parsed: ListFile | None) -> int:
    # A fresh engine per invocation: the CLI is a short-lived process with its own event loop.
    engine = build_engine(settings.database_url)
    try:
        if args.command == "load":
            assert parsed is not None  # noqa: S101 - set by main() for this command
            return await _load(engine, parsed)
        if args.command == "draw":
            return await _draw(engine, redraw=args.redraw)
        if args.command == "new-code":
            return await _new_code(engine, args.name)
        if args.command == "status":
            return await _status(engine)
        if args.command == "wipe":
            return await _wipe(engine)
        raise AssertionError(args.command)  # pragma: no cover - argparse enforces the choices
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Keep stdout for the command's own output (Codes); diagnostics go to stderr.
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    parsed: ListFile | None = None
    if args.command == "load":
        parsed = _read_list(args.list_file)  # validate everything before touching the database
        if parsed is None:
            return 1
    return asyncio.run(_run(args, parsed))


if __name__ == "__main__":
    sys.exit(main())
