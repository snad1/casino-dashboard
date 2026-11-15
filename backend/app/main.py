"""FastAPI application factory, served with `uvicorn app.main:create_app --factory`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, status
from pydantic import BaseModel

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

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(
        title="casino-dashboard",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.sessionmaker = create_sessionmaker(engine)
    install_error_handlers(app, settings.exposes_error_detail)
    # Added innermost first: headers wrap everything, including the generic 500.
    app.add_middleware(UnhandledErrorMiddleware, expose_detail=settings.exposes_error_detail)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, public_host=settings.public_host)

    @app.get("/healthz", response_model=Health, status_code=status.HTTP_200_OK)
    async def healthz() -> Health:
        return Health(status="ok")

    return app
