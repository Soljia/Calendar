"""Application entry point."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from .config import get_settings
from .database import init_db, session_scope
from .logging_config import configure_logging
from .models import AdminUser, utcnow
from .security import hash_password, verify_password
from .services.retention import retention_loop
from .services.settings_store import ensure_settings
from .services.simulator import simulator
from .services.websocket import manager

logger = logging.getLogger(__name__)

DESCRIPTION = """
Monitors Zoom in-meeting chat from every breakout room in one dashboard.

Zoom delivers `meeting.chat_message_sent` events to `/api/zoom/webhook`; each
message is attributed to a breakout room via `breakout_room_uuid`, normalised,
stored, and pushed to connected dashboards over WebSocket.
"""


def bootstrap_admin() -> None:
    """Create or update the local admin account from the environment."""
    settings = get_settings()
    username = settings.admin_username.strip()
    password = settings.admin_password

    if not username:
        logger.error("admin_username_not_configured")
        return

    with session_scope() as db:
        user = db.execute(
            select(AdminUser).where(AdminUser.username == username)
        ).scalar_one_or_none()

        if user is None:
            if not password:
                logger.error(
                    "admin_password_not_configured",
                    extra={"hint": "set ADMIN_PASSWORD; no admin account was created"},
                )
                return
            db.add(
                AdminUser(
                    username=username,
                    password_hash=hash_password(password),
                    token_version=1,
                )
            )
            logger.info("admin_account_created", extra={"user": username})
            return

        # Rotating ADMIN_PASSWORD in the environment rotates the stored hash
        # and invalidates every session issued against the old one.
        if password and not verify_password(password, user.password_hash):
            user.password_hash = hash_password(password)
            user.token_version += 1
            user.updated_at = utcnow()
            logger.info("admin_password_rotated", extra={"user": username})


def _warn_about_configuration() -> None:
    settings = get_settings()
    if settings.session_secret_is_ephemeral:
        logger.warning(
            "session_secret_not_configured",
            extra={
                "impact": "sessions are signed with a random per-process key "
                "and will not survive a restart; set SESSION_SECRET"
            },
        )
    if not settings.zoom_webhook_secret_token:
        logger.warning(
            "zoom_webhook_secret_token_not_configured",
            extra={"impact": "every webhook delivery will be rejected"},
        )
    if not settings.admin_password:
        logger.warning("admin_password_not_set", extra={"impact": "nobody can sign in"})
    if settings.dev_mode:
        logger.warning(
            "dev_mode_enabled",
            extra={"impact": "simulation endpoints are exposed; do not use in production"},
        )
    if settings.display_private_messages:
        logger.warning(
            "private_message_display_enabled",
            extra={"impact": "direct messages between participants are stored and shown"},
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info(
        "application_starting",
        extra={"dev_mode": settings.dev_mode, "log_level": settings.log_level},
    )

    init_db()
    bootstrap_admin()
    with session_scope() as db:
        ensure_settings(db)
    _warn_about_configuration()

    stop_event = asyncio.Event()
    retention_task = asyncio.create_task(retention_loop(stop_event), name="retention")

    try:
        yield
    finally:
        logger.info("application_stopping")
        stop_event.set()
        if simulator.status().running:
            await simulator.stop()
        retention_task.cancel()
        try:
            await retention_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        await manager.close_all()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    if settings.cors_allow_origins:
        # Empty by default: the shipped deployment is single-origin.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Accept"],
        )

    @app.middleware("http")
    async def limit_request_size(request: Request, call_next):
        """Reject oversized bodies before any handler reads them."""
        declared = request.headers.get("content-length")
        if declared:
            try:
                if int(declared) > settings.max_request_bytes:
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={"detail": "Request body too large"},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"detail": "Malformed Content-Length"},
                )
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    # Routers are imported here so that importing app.main stays cheap for
    # tooling that only needs create_app.
    from .api import auth, dev, meetings, rooms, settings as settings_api, webhook, ws

    app.include_router(auth.router)
    app.include_router(webhook.router)
    app.include_router(meetings.router)
    app.include_router(rooms.router)
    app.include_router(settings_api.router)
    app.include_router(ws.router)

    if settings.dev_mode:
        app.include_router(dev.router)
        logger.warning("dev_routes_mounted", extra={"prefix": "/api/dev"})

    @app.get("/api/health", tags=["meta"])
    def health() -> dict:
        return {
            "status": "ok",
            "dev_mode": settings.dev_mode,
            "websocket_clients": manager.client_count(),
        }

    return app


app = create_app()
