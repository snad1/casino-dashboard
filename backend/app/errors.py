"""Error responses that never leak internals outside local, development and testing."""

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging import current_request_id

GENERIC_ERROR = "Something went wrong on our end. Please try again."
# Pydantic error keys that can repeat what the client sent (passwords included).
_ECHO_KEYS = frozenset({"input", "ctx", "url"})

log = structlog.get_logger("app.errors")


class UnhandledErrorMiddleware:
    """Turn an unhandled exception into a 500 the outer middlewares can still decorate.

    Starlette's own handler sits outside every user middleware, so its 500 would miss the
    request id and security headers. The full exception is always logged; the body carries
    `detail` only when `expose_detail` is set.
    """

    def __init__(self, app: ASGIApp, expose_detail: bool) -> None:
        self.app = app
        self.expose_detail = expose_detail

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            log.exception("unhandled_error", path=scope["path"])
            if response_started:
                raise
            body: dict[str, Any] = {"error": GENERIC_ERROR, "request_id": current_request_id()}
            if self.expose_detail:
                body["detail"] = f"{type(exc).__name__}: {exc}"
            await JSONResponse(body, status_code=500)(scope, receive, send)


def install_error_handlers(app: FastAPI, expose_detail: bool) -> None:
    """Validation errors keep their shape but drop the echoed input outside detail envs."""

    async def on_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = jsonable_encoder(exc.errors())
        if not expose_detail:
            errors = [{k: v for k, v in e.items() if k not in _ECHO_KEYS} for e in errors]
        return JSONResponse({"detail": errors}, status_code=422)

    app.add_exception_handler(RequestValidationError, on_validation_error)  # type: ignore[arg-type]
