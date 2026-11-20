"""Web Push: which subscriptions are accepted, what a notification says, and sending one.

A subscription's endpoint is a URL the mailer will POST to, so only https URLs on the known
browser push services are accepted (anything else would let a user point the mailer at an
internal address). The endpoint is also a capability, so it is never logged. Payloads are
short JSON the service worker shows as plain text: in private mode only "New alert", so a
locked screen shows no numbers. Messages are signed with the dashboard's VAPID key and kept
by the push service for 12 hours at most.
"""

import base64
import binascii
import json
from dataclasses import dataclass
from urllib.parse import urlsplit

import aiohttp
from py_vapid import Vapid  # type: ignore[import-untyped]  # ships no type hints
from pywebpush import WebPushException, webpush_async  # type: ignore[import-untyped]  # ships no type hints

PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com")
PUSH_HOST_SUFFIXES = (".push.apple.com", ".notify.windows.com")
ENDPOINT_MAX = 2048
PAYLOAD_MAX = 1024
TTL_S = 12 * 3600
TIMEOUT_S = 10.0
TITLE = "Casino dashboard"
PRIVATE_BODY = "New alert"
OPEN_PATH = "/alerts"


def endpoint_allowed(url: str) -> bool:
    """True for an https URL on a known push service, with no user, port or odd parts."""
    if len(url) > ENDPOINT_MAX:
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, 443):
        return False
    return host in PUSH_HOSTS or host.endswith(PUSH_HOST_SUFFIXES)


def _b64url(value: str) -> bytes | None:
    if not value or len(value) > 200 or any(c in value for c in "+/= \n"):
        return None
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError):
        return None


def keys_valid(p256dh: str, auth: str) -> bool:
    """The browser's keys: an uncompressed P-256 point and a 16-byte auth secret."""
    point, secret = _b64url(p256dh), _b64url(auth)
    return (
        point is not None
        and len(point) == 65
        and point[0] == 4
        and secret is not None
        and len(secret) == 16
    )


def payload(message: str, private: bool) -> str:
    """The JSON the service worker shows; the text is cut so the whole stays under the cap."""
    body = PRIVATE_BODY if private else message
    doc = {"title": TITLE, "body": body, "url": OPEN_PATH}
    text = json.dumps(doc, ensure_ascii=False)
    while len(text.encode()) > PAYLOAD_MAX and doc["body"]:
        doc["body"] = doc["body"][: max(0, len(doc["body"]) - 64)]
        text = json.dumps(doc, ensure_ascii=False)
    return text


class PushError(Exception):
    """A failed send; `gone` when the subscription no longer exists, `permanent` when the
    push service refused the message itself."""

    def __init__(
        self,
        reason: str,
        *,
        gone: bool = False,
        permanent: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(reason)
        self.gone = gone
        self.permanent = permanent
        self.retry_after = retry_after


@dataclass(frozen=True)
class Subscription:
    endpoint: str
    p256dh: str
    auth: str


class PushSender:
    """Sends one notification to one browser subscription, signed with the VAPID key."""

    def __init__(self, private_key_pem: str, contact: str) -> None:
        self._vapid = Vapid.from_pem(private_key_pem.encode())
        self._subject = f"mailto:{contact}"

    async def send(self, subscription: Subscription, data: str) -> None:
        if not endpoint_allowed(subscription.endpoint):
            raise PushError("push endpoint not allowed", gone=True)
        info = {
            "endpoint": subscription.endpoint,
            "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
        }
        try:
            await webpush_async(
                info,
                data=data,
                vapid_private_key=self._vapid,
                vapid_claims={"sub": self._subject},
                ttl=TTL_S,
                timeout=TIMEOUT_S,
                headers={"Urgency": "high"},
            )
        except WebPushException as error:
            raise _failure(error) from None
        except (aiohttp.ClientError, OSError, TimeoutError) as error:
            raise PushError(f"push service unreachable: {type(error).__name__}") from None


def _failure(error: WebPushException) -> PushError:
    status = getattr(error, "status_code", None)
    if status in (404, 410):
        return PushError(f"push subscription gone: HTTP {status}", gone=True)
    if status == 429:
        retry = getattr(error, "retry_after", None)
        wait = float(retry) if isinstance(retry, str) and retry.isdigit() else None
        return PushError("push service rate limited", retry_after=wait)
    if status in (400, 403, 413):
        return PushError(f"push service refused: HTTP {status}", permanent=True)
    if status is None:
        return PushError("push message could not be built", permanent=True)
    return PushError(f"push service failed: HTTP {status}")
