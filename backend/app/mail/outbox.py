"""Queue a message; the caller commits it together with the change it reports."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.mail.models import OutboxMessage
from app.mail.templates import Rendered


def enqueue(db: AsyncSession, to_email: str, message: Rendered) -> None:
    db.add(
        OutboxMessage(
            to_email=to_email,
            subject=message.subject,
            text_body=message.text,
            html_body=message.html,
        )
    )
