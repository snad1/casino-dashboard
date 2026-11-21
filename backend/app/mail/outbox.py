"""Queue a message; the caller commits it together with the change it reports.

Failed sends back off from 30 s, doubling up to an hour, and a message is dead after
`MAX_ATTEMPTS`; alert deliveries follow the same policy.
"""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.mail.models import OutboxMessage
from app.mail.templates import Rendered

MAX_ATTEMPTS = 8
BASE_DELAY = timedelta(seconds=30)
MAX_DELAY = timedelta(hours=1)


def retry_delay(attempts: int) -> timedelta:
    delay: timedelta = BASE_DELAY * (2 ** (attempts - 1))
    return min(delay, MAX_DELAY)


def enqueue(db: AsyncSession, to_email: str | None, message: Rendered) -> None:
    """Queue one message; an account without an email (a Telegram user) gets none."""
    if to_email is None:
        return
    db.add(
        OutboxMessage(
            to_email=to_email,
            subject=message.subject,
            text_body=message.text,
            html_body=message.html,
        )
    )
