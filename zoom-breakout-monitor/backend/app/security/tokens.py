"""Session tokens (JWT) carried in an HttpOnly cookie.

Tokens are never written to localStorage by the frontend; the cookie is the
only place a session lives, which keeps it out of reach of page scripts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import jwt

ALGORITHM = "HS256"
ISSUER = "zoom-breakout-monitor"


class InvalidToken(Exception):
    pass


@dataclass(frozen=True)
class SessionClaims:
    user_id: str
    username: str
    token_version: int
    expires_at: datetime


def create_session_token(
    *,
    user_id: str,
    username: str,
    token_version: int,
    secret: str,
    ttl_hours: int,
) -> str:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=ttl_hours)
    payload: Dict[str, Any] = {
        "sub": user_id,
        "name": username,
        "ver": token_version,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "iss": ISSUER,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_session_token(token: str, secret: str) -> SessionClaims:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={"require": ["exp", "sub", "iss"]},
        )
    except jwt.PyJWTError as exc:  # covers expiry, bad signature, bad issuer
        raise InvalidToken(str(exc)) from exc

    return SessionClaims(
        user_id=str(payload["sub"]),
        username=str(payload.get("name", "")),
        token_version=int(payload.get("ver", 1)),
        expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc),
    )
