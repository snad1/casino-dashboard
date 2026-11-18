"""Mail transports: Brevo's transactional API, or an SMTP relay that must offer STARTTLS."""

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

import httpx2
from starlette.concurrency import run_in_threadpool

from app.settings import MailerSettings

BREVO_URL = "https://api.brevo.com/v3/smtp/email"
TIMEOUT_SECONDS = 10.0


class MailError(Exception):
    """A send failed; the message says which kind of failure, never a credential."""


@dataclass(frozen=True)
class Message:
    to_email: str
    subject: str
    text: str
    html: str


class Mailer(Protocol):
    async def send(self, message: Message) -> None: ...


class BrevoApiMailer:
    def __init__(
        self,
        api_key: str,
        sender: str,
        sender_name: str,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._sender = {"email": sender, "name": sender_name}
        self._transport = transport

    async def send(self, message: Message) -> None:
        payload = {
            "sender": self._sender,
            "to": [{"email": message.to_email}],
            "subject": message.subject,
            "textContent": message.text,
            "htmlContent": message.html,
        }
        headers = {"api-key": self._api_key, "accept": "application/json"}
        try:
            async with httpx2.AsyncClient(
                timeout=TIMEOUT_SECONDS, transport=self._transport
            ) as client:
                response = await client.post(BREVO_URL, json=payload, headers=headers)
        except httpx2.HTTPError as error:
            raise MailError(f"brevo unreachable: {type(error).__name__}") from None
        if response.status_code >= 300:
            raise MailError(f"brevo refused the message: HTTP {response.status_code}")


class SmtpMailer:
    def __init__(
        self, host: str, port: int, user: str, password: str, sender: str, sender_name: str
    ) -> None:
        self._host, self._port = host, port
        self._user, self._password = user, password
        self._from = f"{sender_name} <{sender}>"

    def _send_blocking(self, message: Message) -> None:
        mail = EmailMessage()
        mail["From"] = self._from
        mail["To"] = message.to_email
        mail["Subject"] = message.subject
        mail.set_content(message.text)
        mail.add_alternative(message.html, subtype="html")
        try:
            with smtplib.SMTP(self._host, self._port, timeout=TIMEOUT_SECONDS) as smtp:
                smtp.ehlo()
                if not smtp.has_extn("starttls"):
                    raise MailError("smtp relay does not offer STARTTLS; refusing to send")
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                smtp.login(self._user, self._password)
                smtp.send_message(mail)
        except (smtplib.SMTPException, OSError) as error:
            raise MailError(f"smtp send failed: {type(error).__name__}") from None

    async def send(self, message: Message) -> None:
        await run_in_threadpool(self._send_blocking, message)


def mailer_from(settings: MailerSettings) -> Mailer:
    if settings.mail_backend == "brevo" and settings.brevo_api_key is not None:
        return BrevoApiMailer(
            settings.brevo_api_key.get_secret_value(), settings.mail_from, settings.mail_from_name
        )
    if settings.smtp_host and settings.smtp_user and settings.smtp_password:
        return SmtpMailer(
            settings.smtp_host,
            settings.smtp_port,
            settings.smtp_user,
            settings.smtp_password.get_secret_value(),
            settings.mail_from,
            settings.mail_from_name,
        )
    raise MailError("no mail backend configured")
