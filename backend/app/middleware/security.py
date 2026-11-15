"""HTTP security headers on every API response, including errors and 404s."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def security_headers(public_host: str) -> dict[str, str]:
    """Headers for an API that serves JSON and opens WebSockets to its own host only."""
    return {
        "content-security-policy": (
            "default-src 'self'; script-src 'self'; "
            f"connect-src 'self' wss://{public_host}; "
            "frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
        ),
        "strict-transport-security": "max-age=31536000; includeSubDomains",
        "referrer-policy": "no-referrer",
        "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=()",
        "x-content-type-options": "nosniff",
    }


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, public_host: str) -> None:
        self.app = app
        self.headers = security_headers(public_host)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self.headers.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)
