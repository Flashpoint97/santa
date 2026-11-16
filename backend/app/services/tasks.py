"""Background tasks: run after the response is sent, each with its own DB session.

Failures are logged and counted, never raised into the request that scheduled them.
"""

import uuid

import structlog
from sqlalchemy import select

from app.config import settings
from app.db.models import Event, Participant
from app.db.session import SessionLocal
from app.metrics import EMAILS_SENT
from app.services.auth import CodeToSend
from app.services.email import EmailSender, draw_done_message, login_code_message

logger = structlog.get_logger(__name__)


async def send_login_code(sender: EmailSender, item: CodeToSend) -> None:
    try:
        await sender.send(
            login_code_message(item.email, item.code, settings.login_code_ttl_minutes)
        )
    except Exception:
        EMAILS_SENT.labels(kind="login_code", outcome="failure").inc()
        # No code, no address in the log line: participant id is enough to investigate.
        logger.exception(
            "email_send_failed", kind="login_code", participant_id=str(item.participant_id)
        )
        return
    EMAILS_SENT.labels(kind="login_code", outcome="success").inc()
    logger.info("email_sent", kind="login_code", participant_id=str(item.participant_id))


async def notify_draw_done(sender: EmailSender, event_id: uuid.UUID) -> None:
    async with SessionLocal() as session:
        try:
            event = await session.scalar(select(Event).where(Event.id == event_id))
            if event is None:
                return
            people = list(
                await session.scalars(select(Participant).where(Participant.event_id == event_id))
            )
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("draw_notify_load_failed", event_id=str(event_id))
            return

    for p in people:
        try:
            await sender.send(draw_done_message(p.email, p.name, event.name, settings.frontend_url))
        except Exception:
            EMAILS_SENT.labels(kind="draw_done", outcome="failure").inc()
            logger.exception("email_send_failed", kind="draw_done", participant_id=str(p.id))
            continue
        EMAILS_SENT.labels(kind="draw_done", outcome="success").inc()
    logger.info("draw_notifications_sent", event_id=str(event_id), count=len(people))
