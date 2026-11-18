"""Structured JSON logging with secret redaction and a request id per request."""

import logging
import re
import sys
import time
import uuid
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.settings import Settings

REDACTED = "[redacted]"
REQUEST_ID_HEADER = "x-request-id"
_SENSITIVE_KEY = re.compile(r"secret|token|password|api_key|cookie|authorization", re.IGNORECASE)
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9-]{8,64}")

log = structlog.get_logger("app.request")


def scrub(value: Any) -> Any:
    """Replace the value of every secret-looking key, at any depth."""
    if isinstance(value, Mapping):
        return {
            key: REDACTED if isinstance(key, str) and _SENSITIVE_KEY.search(key) else scrub(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [scrub(item) for item in value]
    return value


def redact(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """structlog processor: replace the value of every secret-looking key, at any depth."""
    scrubbed: MutableMapping[str, Any] = scrub(event_dict)
    return scrubbed


def configure_logging(settings: Settings) -> None:
    """Route structlog and stdlib logging to stdout: JSON, or readable lines when local."""
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact,
    ]
    renderer: Any = (
        structlog.dev.ConsoleRenderer()
        if settings.app_env == "local"
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                renderer,
            ],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    # uvicorn's access log includes query strings; RequestContextMiddleware logs requests instead.
    logging.getLogger("uvicorn.access").disabled = True


def _inbound_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == REQUEST_ID_HEADER.encode():
            candidate = value.decode("latin-1")
            return candidate if _VALID_REQUEST_ID.fullmatch(candidate) else None
    return None


def current_request_id() -> str | None:
    """The id bound to the request being handled, if any."""
    request_id: str | None = structlog.contextvars.get_contextvars().get("request_id")
    return request_id


class RequestContextMiddleware:
    """Bind a request id to every log line, echo it in the response and log one line per request.

    An inbound `X-Request-ID` (from Caddy or a client) is kept only when it is a plain token;
    anything else is replaced, so the header cannot inject text into logs. Only the path is
    logged: query strings can carry tokens.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = _inbound_request_id(scope) or uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        status = 500
        started = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            log.info(
                "request",
                method=scope["method"],
                path=scope["path"],
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
