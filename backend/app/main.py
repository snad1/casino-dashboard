"""FastAPI application factory, served with `uvicorn app.main:create_app --factory`."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import structlog
from fastapi import FastAPI, status
from pydantic import BaseModel
from redis.asyncio import Redis

from app.adapters.base import ServiceAdapter
from app.adapters.wolfbet import WolfBetAdapter
from app.agent_client import AgentClient
from app.alerts import digest
from app.alerts.engine import Engine
from app.api.alerts import router as alerts_router
from app.api.analytics import router as analytics_router
from app.api.config import router as config_router
from app.api.control import router as control_router
from app.api.files import router as files_router
from app.api.history import router as history_router
from app.audit import AuditMiddleware
from app.audit import router as audit_router
from app.auth.admin import router as admin_router
from app.auth.csrf import CsrfMiddleware
from app.auth.invites import router as invites_router
from app.auth.reset import router as reset_router
from app.auth.routes import router as auth_router
from app.auth.stepup import install_step_handler
from app.auth.stepup import router as step_up_router
from app.auth.totp import router as totp_router
from app.comments import router as comments_router
from app.db.engine import create_engine, create_sessionmaker
from app.errors import UnhandledErrorMiddleware, install_error_handlers
from app.live.aggregator import Aggregator, TickTimer, run_ticker
from app.live.consumer import ServiceConsumer
from app.live.resync import Resync
from app.live.system import SystemPublisher
from app.logging import RequestContextMiddleware, configure_logging
from app.metrics import DropWatch
from app.metrics import router as metrics_router
from app.middleware.security import SecurityHeadersMiddleware
from app.settings import Settings, load_settings
from app.ws.auth import RECHECK_SECONDS, ConnectionRegistry
from app.ws.hub import Hub
from app.ws.routes import router as ws_router
from app.ws.terminal import router as terminal_router

ADAPTERS: dict[str, type[ServiceAdapter]] = {"wolfbet": WolfBetAdapter}

log = structlog.get_logger("app.main")


class Health(BaseModel):
    status: Literal["ok"]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API app; loading settings first makes a bad secret stop startup."""
    settings = settings or load_settings()
    configure_logging(settings)
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    redis = Redis.from_url(settings.redis_url)
    session_secret = settings.session_secret.get_secret_value()
    agent = None
    if settings.agent_socket is not None and settings.agent_token is not None:
        agent = AgentClient(settings.agent_socket, settings.agent_token.get_secret_value())
    hub: Hub | None = None
    alerts: Engine | None = None
    consumer: ServiceConsumer | None = None
    system: SystemPublisher | None = None
    ticks = TickTimer()
    if settings.live_service is not None:
        if settings.live_service not in ADAPTERS:
            raise ValueError(f"no live adapter for service {settings.live_service!r}")
        aggregator = Aggregator(settings.live_service)
        hub = Hub(settings.live_service, aggregator)
        consumer = ServiceConsumer(redis, ADAPTERS[settings.live_service](), aggregator.accept)
        resync = Resync(
            aggregator, sessionmaker, agent, reconnects=lambda: consumer.stats["reconnects"]
        )
        system = SystemPublisher(hub, aggregator, agent)
        alerts = Engine(settings.live_service, sessionmaker, settings.host_tz)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Run the live pipeline (stream to model to sockets) for the app's whole life."""
        stop = asyncio.Event()
        tasks = []
        if hub is not None and alerts is not None and consumer is not None and system is not None:
            try:
                await alerts.start()
            except Exception:
                log.exception("alert rules could not be loaded; alerts are off until restart")
            tasks = [
                asyncio.create_task(consumer.run(stop)),
                asyncio.create_task(
                    run_ticker(aggregator, hub.publish, stop, settings.live_hz, ticks)
                ),
                asyncio.create_task(alerts.run(stop, aggregator)),
                asyncio.create_task(
                    digest.run(stop, sessionmaker, alerts.service, settings.host_tz)
                ),
                asyncio.create_task(hub.run_heartbeat(stop)),
                asyncio.create_task(resync.run(stop)),
                asyncio.create_task(system.run(stop)),
            ]
        yield
        stop.set()
        await asyncio.gather(*tasks)
        await engine.dispose()
        await redis.aclose()

    app = FastAPI(
        title="casino-dashboard",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.sessionmaker = sessionmaker
    app.state.redis = redis
    app.state.session_secret = session_secret
    app.state.totp_key = settings.totp_key.get_secret_value()
    app.state.public_origin = settings.public_origin
    app.state.allowed_origins = settings.allowed_origins
    app.state.ws_registry = ConnectionRegistry()
    app.state.ws_recheck_seconds = RECHECK_SECONDS
    app.state.hub = hub
    app.state.agent = agent
    app.state.host_tz = settings.host_tz
    app.state.alerts = alerts
    app.state.telegram_bot_username = settings.telegram_bot_username
    app.state.vapid_public_key = settings.vapid_public_key
    app.state.live_service = settings.live_service
    app.state.consumer = consumer
    app.state.system = system
    app.state.ticks = ticks
    app.state.drops = DropWatch()
    app.state.terminal_ip_allowlist = settings.terminal_ip_allowlist
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
    app.include_router(admin_router)
    app.include_router(history_router)
    app.include_router(analytics_router)
    app.include_router(alerts_router)
    app.include_router(control_router)
    app.include_router(config_router)
    app.include_router(files_router)
    app.include_router(comments_router)
    app.include_router(ws_router)
    app.include_router(terminal_router)
    app.include_router(metrics_router)

    return app
