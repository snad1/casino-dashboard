"""FastAPI application factory, served with `uvicorn app.main:create_app --factory`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, status
from pydantic import BaseModel
from redis.asyncio import Redis

from app.audit import AuditMiddleware
from app.audit import router as audit_router
from app.auth.csrf import CsrfMiddleware
from app.auth.invites import router as invites_router
from app.auth.reset import router as reset_router
from app.auth.routes import router as auth_router
from app.auth.stepup import install_step_handler
from app.auth.stepup import router as step_up_router
from app.auth.totp import router as totp_router
from app.db.engine import create_engine, create_sessionmaker
from app.errors import UnhandledErrorMiddleware, install_error_handlers
from app.logging import RequestContextMiddleware, configure_logging
from app.middleware.security import SecurityHeadersMiddleware
from app.settings import Settings, load_settings


class Health(BaseModel):
    status: Literal["ok"]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API app; loading settings first makes a bad secret stop startup."""
    settings = settings or load_settings()
    configure_logging(settings)
    engine = create_engine(settings)
    redis = Redis.from_url(settings.redis_url)
    session_secret = settings.session_secret.get_secret_value()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()
        await redis.aclose()

    app = FastAPI(
        title="casino-dashboard",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.sessionmaker = create_sessionmaker(engine)
    app.state.redis = redis
    app.state.session_secret = session_secret
    app.state.totp_key = settings.totp_key.get_secret_value()
    app.state.public_origin = settings.public_origin
    install_error_handlers(app, settings.exposes_error_detail)
    install_step_handler(app)
    # Added innermost first: headers wrap everything, including the generic 500.
    app.add_middleware(
        CsrfMiddleware, secret=session_secret, allowed_origins=settings.allowed_origins
    )
    app.add_middleware(AuditMiddleware, sessionmaker=app.state.sessionmaker)
    app.add_middleware(UnhandledErrorMiddleware, expose_detail=settings.exposes_error_detail)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, public_host=settings.public_host)

    @app.get("/healthz", response_model=Health, status_code=status.HTTP_200_OK)
    async def healthz() -> Health:
        return Health(status="ok")

    app.include_router(auth_router)
    app.include_router(totp_router)
    app.include_router(step_up_router)
    app.include_router(invites_router)
    app.include_router(reset_router)
    app.include_router(audit_router)

    return app
