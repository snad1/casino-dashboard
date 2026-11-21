"""CSRF check on every state-changing request.

A request that carries the session cookie must send the session's token in `X-CSRF-Token`.
Login has no session yet, so it must come from an allowed `Origin` instead; that stops a
foreign page from signing the browser into another account. A request without the cookie
has no ambient credential to abuse, and any route that needs a user refuses it anyway.
"""

import hmac
import json

from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth.sessions import SESSION_COOKIE, csrf_token, decode_session_id

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOGIN_PATHS = frozenset({"/auth/login", "/auth/telegram"})
CSRF_HEADER = "x-csrf-token"
CSRF_FAILED = "Your session expired. Reload the page and try again."


class CsrfMiddleware:
    def __init__(self, app: ASGIApp, secret: str, allowed_origins: list[str]) -> None:
        self.app = app
        self.secret = secret
        self.allowed_origins = frozenset(allowed_origins)

    def _allowed(self, scope: Scope) -> bool:
        headers = Headers(scope=scope)
        if scope["path"] in LOGIN_PATHS:
            return headers.get("origin") in self.allowed_origins
        cookie = cookie_parser(headers.get("cookie", "")).get(SESSION_COOKIE)
        if cookie is None:
            return True
        raw = decode_session_id(cookie)
        sent = headers.get(CSRF_HEADER, "")
        return raw is not None and hmac.compare_digest(sent, csrf_token(self.secret, raw))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS or self._allowed(scope):
            await self.app(scope, receive, send)
            return
        body = json.dumps({"detail": CSRF_FAILED}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
