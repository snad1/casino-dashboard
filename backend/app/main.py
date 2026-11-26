"""FastAPI application factory, served with `uvicorn app.main:create_app --factory`."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import structlog
from fastapi import FastAPI, status
from pydantic import BaseModel
from redis.asyncio import Redis

from app import registry
from app.adapters.wolfbet_tg import WolfBetTgAdapter
from app.agent_client import AgentClient
from app.api.alerts import router as alerts_router
from app.api.analytics import router as analytics_router
from app.api.config import router as config_router
from app.api.control import router as control_router
from app.api.files import router as files_router
from app.api.history import router as history_router
from app.api.keys import router as keys_router
from app.api.services import router as services_router
from app.api.tg import router as tg_router
from app.audit import AuditMiddleware
from app.audit import router as audit_router
from app.auth.admin import router as admin_router
from app.auth.csrf import CsrfMiddleware
from app.auth.invites import router as invites_router
from app.auth.key_lanes import KEY_LANES
from app.auth.reset import router as reset_router
from app.auth.routes import router as auth_router
from app.auth.stepup import install_step_handler
from app.auth.stepup import router as step_up_router
from app.auth.telegram import router as telegram_router
from app.auth.totp import router as totp_router
from app.comments import router as comments_router
from app.db.engine import create_engine, create_sessionmaker
from app.db.services import register_service
from app.errors import UnhandledErrorMiddleware, install_error_handlers
from app.live.aggregator import TickTimer, run_ticker
from app.live.consumer import ServiceConsumer
from app.live.pipeline import LivePipeline
from app.live.pipeline import build as build_pipeline
from app.live.users import UserAggregators, known_uids
from app.logging import RequestContextMiddleware, configure_logging
from app.metrics import DropWatch
from app.metrics import router as metrics_router
from app.middleware.security import SecurityHeadersMiddleware
from app.settings import Settings, load_settings
from app.ws.auth import RECHECK_SECONDS, ConnectionRegistry
from app.ws.hub import Hub
from app.ws.routes import router as ws_router
from app.ws.terminal import router as terminal_router

PER_USER_ADAPTERS = {"wolfbet-tg": WolfBetTgAdapter}

log = structlog.get_logger("app.main")


class Health(BaseModel):
    status: Literal["ok"]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API app; loading settings first makes a bad secret stop startup."""
    settings = settings or load_settings()
    registry.enable(settings.dashboard_services)
    configure_logging(settings)
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    redis = Redis.from_url(settings.redis_url)
    session_secret = settings.session_secret.get_secret_value()
    agent = None
    if settings.agent_socket is not None and settings.agent_token is not None:
        agent = AgentClient(settings.agent_socket, settings.agent_token.get_secret_value())
    pipelines: list[LivePipeline] = []
    users: UserAggregators | None = None
    user_hub: Hub | None = None
    user_consumer: ServiceConsumer | None = None
    ticks = TickTimer()
    user_svc = settings.live_user_service
    if settings.live_service in PER_USER_ADAPTERS:
        user_svc = settings.live_service
    if user_svc is not None:
        if user_svc not in PER_USER_ADAPTERS:
            raise ValueError(f"no per-user live adapter for service {user_svc!r}")
        users = UserAggregators(user_svc)
        user_hub = Hub(user_svc, users)
        tg = PER_USER_ADAPTERS[user_svc]()

        async def discover() -> tuple[str, ...]:
            return tg.streams_for(await known_uids(sessionmaker, tg.name))

        user_consumer = ServiceConsumer(redis, tg, users.accept, discover=discover)
    if settings.live_service is not None and settings.live_service not in PER_USER_ADAPTERS:
        if not registry.is_enabled(settings.live_service):
            raise ValueError(f"LIVE_SERVICE {settings.live_service} is not in DASHBOARD_SERVICES")
        # Every served service runs live; LIVE_SERVICE is the one a socket joins first and
        # the one the metrics describe.
        pipelines = [
            build_pipeline(name, redis, sessionmaker, agent, settings.host_tz)
            for name in registry.enabled()
        ]
    primary = next((p for p in pipelines if p.svc == settings.live_service), None)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Run the live pipelines (stream to model to sockets) for the app's whole life.

        A per-user bot gets the stream, the per-user model and the sockets only: resync,
        system metrics, alerts and the digest read the daemon through the host agent.
        """
        stop = asyncio.Event()
        tasks = []
        for name in registry.enabled():
            await register_service(sessionmaker, name, settings.public_host)
        if users is not None and user_hub is not None and user_consumer is not None:
            tasks = [
                asyncio.create_task(user_consumer.run(stop)),
                asyncio.create_task(
                    run_ticker(users, user_hub.publish, stop, settings.live_hz, ticks)
                ),
                asyncio.create_task(user_hub.run_heartbeat(stop)),
            ]
        for pipeline in pipelines:
            await pipeline.start_alerts()
            tasks += pipeline.tasks(stop, settings.live_hz, ticks, sessionmaker, settings.host_tz)
        yield
        stop.set()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        for task, outcome in zip(tasks, outcomes, strict=True):
            if isinstance(outcome, Exception):
                log.error("live task ended with an error", task=task.get_name(), exc_info=outcome)
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
    app.state.hub = primary.hub if primary is not None else user_hub
    app.state.hubs = {p.svc: p.hub for p in pipelines} | (
        {user_hub.svc: user_hub} if user_hub else {}
    )
    app.state.agent = agent
    KEY_LANES.use(agent)
    app.state.host_tz = settings.host_tz
    app.state.pipelines = pipelines
    app.state.alerts = {p.svc: p.alerts for p in pipelines}
    app.state.telegram_bot_username = settings.telegram_bot_username
    app.state.tg_login_secret = (
        settings.tg_login_secret.get_secret_value() if settings.tg_login_secret else None
    )
    app.state.vapid_public_key = settings.vapid_public_key
    app.state.live_service = settings.live_service
    app.state.consumer = primary.consumer if primary is not None else user_consumer
    app.state.system = primary.system if primary is not None else None
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
    app.include_router(telegram_router)
    app.include_router(totp_router)
    app.include_router(step_up_router)
    app.include_router(invites_router)
    app.include_router(reset_router)
    app.include_router(audit_router)
    app.include_router(admin_router)
    app.include_router(tg_router)
    app.include_router(history_router)
    app.include_router(analytics_router)
    app.include_router(alerts_router)
    app.include_router(keys_router)
    app.include_router(services_router)
    app.include_router(control_router)
    app.include_router(config_router)
    app.include_router(files_router)
    app.include_router(comments_router)
    app.include_router(ws_router)
    app.include_router(terminal_router)
    app.include_router(metrics_router)

    return app
