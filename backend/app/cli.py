"""Organizer CLI: `python -m app.cli <command>`. Runs against whatever DATABASE_URL points at.

Output never includes assignments. Codes are printed once, by `load`, and only their HMAC is stored.
`draw` prints only the participant count.
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
from app.services.drawing import AlreadyDrawnError, DrawImpossibleError, NoEventError, run_draw
from app.services.list_file import ListFile, ListFileError, parse_list_file
from app.services.loader import EventExistsError, load_event


def _read_list(path: Path) -> tuple[ListFile, dict[str, bytes]] | None:
    """Read and validate the list file and convert its GIFs; on any problem report it on
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("load", help="validate a list file, create the event and print Codes")
    load.add_argument("list_file", type=Path)
    draw = sub.add_parser("draw", help="draw the Assignments (prints only the participant count)")
    draw.add_argument("--redraw", action="store_true", help="replace an existing draw, atomically")
    return parser


async def _run(args: argparse.Namespace, prepared: tuple[ListFile, dict[str, bytes]] | None) -> int:
    # A fresh engine per invocation: the CLI is a short-lived process with its own event loop.
    engine = build_engine(settings.database_url)
    try:
        if args.command == "load":
            assert prepared is not None  # noqa: S101 - set by main() for this command
            return await _load(engine, *prepared)
        if args.command == "draw":
            return await _draw(engine, redraw=args.redraw)
        raise AssertionError(args.command)  # pragma: no cover - argparse enforces the choices
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Keep stdout for the command's own output (Codes); diagnostics go to stderr.
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    prepared: tuple[ListFile, dict[str, bytes]] | None = None
    if args.command == "load":
        # Validate the list and convert the GIFs before touching the database.
        prepared = _read_list(args.list_file)
        if prepared is None:
            return 1
    return asyncio.run(_run(args, prepared))


if __name__ == "__main__":
    sys.exit(main())
