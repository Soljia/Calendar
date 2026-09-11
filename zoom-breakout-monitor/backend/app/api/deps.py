"""Shared FastAPI dependencies: authentication, rate limiting, client identity."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..database import get_db
from ..models import AdminUser
from ..security import InvalidToken, RateLimiter, decode_session_token

logger = logging.getLogger(__name__)

login_limiter = RateLimiter(limit=get_settings().login_rate_limit_per_minute)
webhook_limiter = RateLimiter(limit=get_settings().webhook_rate_limit_per_minute)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    username: str


def client_ip(request: Request) -> str:
    """Best-effort client identity for rate limiting.

    ``X-Forwarded-For`` is honoured because the shipped deployment puts nginx
    in front of the app. Behind an untrusted proxy this header is spoofable;
    the README says so.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def settings_dep() -> Settings:
    return get_settings()


def _resolve_user(db: Session, token: str, secret: str) -> CurrentUser | None:
    try:
        claims = decode_session_token(token, secret)
    except InvalidToken:
        return None

    user = db.get(AdminUser, claims.user_id)
    if user is None or user.token_version != claims.token_version:
        # Password changed (or the account is gone) since the token was issued.
        return None
    return CurrentUser(id=user.id, username=user.username)


def current_user(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> CurrentUser:
    token = request.cookies.get(settings.cookie_name)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    user = _resolve_user(db, token, settings.session_secret)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Session is invalid or expired"
        )
    return user


def optional_user(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> CurrentUser | None:
    token = request.cookies.get(settings.cookie_name)
    if not token:
        return None
    return _resolve_user(db, token, settings.session_secret)


def authenticate_websocket_cookie(db: Session, token: str | None) -> CurrentUser | None:
    """Cookie-based auth for the WebSocket handshake.

    Browsers cannot attach custom headers to a WebSocket upgrade, so the
    HttpOnly session cookie -- sent automatically for same-origin requests --
    is the authentication channel. That is one reason the production
    deployment serves the UI and the API from a single origin.
    """
    if not token:
        return None
    return _resolve_user(db, token, get_settings().session_secret)


def enforce_rate_limit(limiter: RateLimiter, request: Request, *, name: str) -> None:
    if not get_settings().rate_limit_enabled:
        return
    key = client_ip(request)
    if not limiter.allow(key):
        logger.warning("rate_limit_exceeded", extra={"endpoint": name})
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
            headers={"Retry-After": str(limiter.retry_after(key))},
        )
