"""Organizer CLI: `python -m app.cli <command>`. Runs against whatever DATABASE_URL points at.

Output never includes assignments. Codes are printed once, by `load`, and only their HMAC is stored.
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
from app.services.list_file import ListFile, ListFileError, parse_list_file
from app.services.loader import EventExistsError, load_event


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("load", help="validate a list file, create the event and print Codes")
    load.add_argument("list_file", type=Path)
    return parser


async def _run(args: argparse.Namespace, parsed: ListFile | None) -> int:
    # A fresh engine per invocation: the CLI is a short-lived process with its own event loop.
    engine = build_engine(settings.database_url)
    try:
        if args.command == "load":
            assert parsed is not None  # noqa: S101 - set by main() for this command
            return await _load(engine, parsed)
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
