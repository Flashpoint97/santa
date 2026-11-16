"""Organizer bootstrap. Run against the production DB once to create the event and its admin:

    uv run python -m app.cli create-event --name "Office 2026" \
        --admin-name "Ana" --admin-email ana@example.com

The admin then logs in on the website and adds everyone else there, so the participant list
never touches the repo.
"""

import argparse
import asyncio
from datetime import date
from decimal import Decimal

from app.db.models import Event, Participant
from app.db.session import SessionLocal, engine


async def create_event(args: argparse.Namespace) -> None:
    async with SessionLocal() as session:
        event = Event(
            name=args.name,
            budget_amount=Decimal(args.budget) if args.budget else None,
            budget_currency=args.currency,
            gift_deadline=date.fromisoformat(args.deadline) if args.deadline else None,
        )
        session.add(event)
        await session.flush()
        session.add(
            Participant(
                event_id=event.id,
                name=args.admin_name,
                email=args.admin_email.strip().lower(),
                is_admin=True,
            )
        )
        await session.commit()
        print(f"Created event {event.id}. The admin can now log in on the website.")
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-event", help="Create an event and its admin participant")
    p.add_argument("--name", required=True)
    p.add_argument("--admin-name", required=True)
    p.add_argument("--admin-email", required=True)
    p.add_argument("--budget", help="e.g. 30")
    p.add_argument("--currency", default="EUR")
    p.add_argument("--deadline", help="YYYY-MM-DD")
    args = parser.parse_args()
    if args.command == "create-event":
        asyncio.run(create_event(args))


if __name__ == "__main__":
    main()
