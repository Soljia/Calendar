"""Local admin authentication.

The session lives only in an HttpOnly cookie -- never in localStorage -- so a
cross-site scripting bug cannot read it out of the page.

The user record is deliberately a first-class table rather than an env-var
comparison, so adding an OIDC/SSO identity provider later means adding a second
way to populate ``CurrentUser`` and issue the same cookie, without touching any
downstream route.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..database import get_db
from ..models import AdminUser
from ..schemas import LoginRequest, SessionInfo
from ..security import create_session_token, decode_session_token, hash_password, verify_password
from .deps import CurrentUser, current_user, enforce_rate_limit, login_limiter, settings_dep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Verified against when the username does not exist, so that a bad username
# and a bad password cost the same amount of bcrypt work.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(24))


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )


@router.post("/login", response_model=SessionInfo)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> SessionInfo:
    enforce_rate_limit(login_limiter, request, name="login")

    user = db.execute(
        select(AdminUser).where(AdminUser.username == payload.username)
    ).scalar_one_or_none()

    # Always run a verification so a missing user and a wrong password take a
    # comparable amount of time.
    stored_hash = user.password_hash if user else _DUMMY_HASH
    password_ok = verify_password(payload.password, stored_hash)
    if user is None or not password_ok:
        logger.warning("login_failed", extra={"username_len": len(payload.username)})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password"
        )

    token = create_session_token(
        user_id=user.id,
        username=user.username,
        token_version=user.token_version,
        secret=settings.session_secret,
        ttl_hours=settings.session_ttl_hours,
    )
    _set_session_cookie(response, token, settings)
    logger.info("login_succeeded", extra={"user": user.username})

    claims = decode_session_token(token, settings.session_secret)
    return SessionInfo(authenticated=True, username=user.username, expires_at=claims.expires_at)


@router.post("/logout", response_model=SessionInfo)
def logout(response: Response, settings: Settings = Depends(settings_dep)) -> SessionInfo:
    response.delete_cookie(
        key=settings.cookie_name,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )
    return SessionInfo(authenticated=False)


@router.get("/session", response_model=SessionInfo)
def session_info(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> SessionInfo:
    """Unauthenticated probe used by the SPA to decide login vs. dashboard."""
    token = request.cookies.get(settings.cookie_name)
    if not token:
        return SessionInfo(authenticated=False)
    try:
        claims = decode_session_token(token, settings.session_secret)
    except Exception:
        return SessionInfo(authenticated=False)

    user = db.get(AdminUser, claims.user_id)
    if user is None or user.token_version != claims.token_version:
        return SessionInfo(authenticated=False)
    return SessionInfo(
        authenticated=True, username=user.username, expires_at=claims.expires_at
    )


@router.get("/me", response_model=SessionInfo)
def me(user: CurrentUser = Depends(current_user)) -> SessionInfo:
    return SessionInfo(authenticated=True, username=user.username)
