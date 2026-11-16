"""Email sending. Called only from background tasks, never on the request path.

Emails are bilingual (Croatian + English) so we don't need a per-person language setting.
Assignments are never put in an email: Postmark keeps message content in its activity log,
and the hard rule is that assignments live only behind the participant's own login.
"""

from dataclasses import dataclass
from typing import Protocol

import httpx
import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    text: str


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


class ConsoleEmailSender:
    """Development only (production config refuses it). Prints the message to stdout."""

    async def send(self, message: EmailMessage) -> None:
        print(f"\n--- email to {message.to} ---\n{message.subject}\n\n{message.text}\n---\n")


class PostmarkEmailSender:
    _URL = "https://api.postmarkapp.com/email"

    def __init__(self, server_token: str, sender: str, timeout_s: float = 10.0) -> None:
        self._token = server_token
        self._sender = sender
        self._timeout = timeout_s

    async def send(self, message: EmailMessage) -> None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                self._URL,
                headers={"X-Postmark-Server-Token": self._token, "Accept": "application/json"},
                json={
                    "From": self._sender,
                    "To": message.to,
                    "Subject": message.subject,
                    "TextBody": message.text,
                    "MessageStream": "outbound",
                    "TrackOpens": False,
                    "TrackLinks": "None",
                },
            )
            # Don't log the body: Postmark echoes the recipient address.
            resp.raise_for_status()


def login_code_message(to: str, code: str, ttl_minutes: int) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject=f"Tajni Djed Božićnjak / Secret Santa: {code}",
        text=(
            f"Tvoj kod za prijavu je: {code}\n"
            f"Vrijedi {ttl_minutes} minuta. Ako nisi zatražio/la kod, zanemari ovu poruku.\n\n"
            f"Your login code is: {code}\n"
            f"It is valid for {ttl_minutes} minutes. If you didn't request it, ignore this email.\n"
        ),
    )


def draw_done_message(to: str, name: str, event_name: str, frontend_url: str) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject=f"{event_name}: izvlačenje je gotovo / the draw is done",
        text=(
            f"Bok {name}!\n"
            f"Izvlačenje za „{event_name}” je obavljeno. Prijavi se da vidiš kome kupuješ poklon:\n"
            f"{frontend_url}\n\n"
            f"Hi {name}!\n"
            f"The draw for “{event_name}” is done. Log in to see who you're buying a gift for:\n"
            f"{frontend_url}\n"
        ),
    )
