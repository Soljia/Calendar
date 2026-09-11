"""Optional Zoom REST calls (Server-to-Server OAuth).

Scope of what this can actually do, and why:

Zoom's ``meeting.chat_message_sent`` webhook carries a ``breakout_room_uuid``
but no human-readable room name, and Zoom exposes no public endpoint that maps
a breakout-room UUID back to a configured room. What *is* retrievable is the
list of breakout rooms an organiser pre-assigned on the meeting
(``GET /meetings/{meetingId}`` -> ``settings.breakout_room.rooms[].name``).

So this module offers those names as *suggestions* for an administrator to
apply, never as an automatic mapping. The core monitor works fully without it:
every call site treats a failure here as "no suggestions available".
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, List

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

_TOKEN_SAFETY_MARGIN_SECONDS = 60


@dataclass
class _CachedToken:
    value: str
    expires_at: float


class ZoomApiError(Exception):
    pass


class ZoomApiClient:
    def __init__(self) -> None:
        self._token: _CachedToken | None = None

    @property
    def configured(self) -> bool:
        return get_settings().zoom_oauth_configured

    async def _access_token(self) -> str:
        settings = get_settings()
        if not settings.zoom_oauth_configured:
            raise ZoomApiError("Zoom Server-to-Server OAuth credentials are not configured")

        now = time.monotonic()
        if self._token and self._token.expires_at > now:
            return self._token.value

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                settings.zoom_oauth_token_url,
                params={
                    "grant_type": "account_credentials",
                    "account_id": settings.zoom_account_id,
                },
                auth=(settings.zoom_client_id, settings.zoom_client_secret),
            )
        if response.status_code != 200:
            logger.warning("zoom_oauth_failed", extra={"status": response.status_code})
            raise ZoomApiError(f"Zoom OAuth token request failed ({response.status_code})")

        body = response.json()
        token = body.get("access_token")
        if not token:
            raise ZoomApiError("Zoom OAuth response did not contain an access_token")
        expires_in = int(body.get("expires_in", 3600))
        self._token = _CachedToken(
            value=token,
            expires_at=now + max(60, expires_in - _TOKEN_SAFETY_MARGIN_SECONDS),
        )
        return token

    async def get_meeting(self, meeting_id: str) -> dict[str, Any]:
        settings = get_settings()
        token = await self._access_token()
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{settings.zoom_api_base_url}/meetings/{meeting_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        if response.status_code == 404:
            raise ZoomApiError("Zoom does not know this meeting ID")
        if response.status_code != 200:
            logger.warning("zoom_api_request_failed", extra={"status": response.status_code})
            raise ZoomApiError(f"Zoom API request failed ({response.status_code})")
        return response.json()

    async def preassigned_room_names(self, meeting_id: str) -> List[str]:
        """Names of breakout rooms pre-assigned on the meeting, if any."""
        meeting = await self.get_meeting(meeting_id)
        breakout = ((meeting.get("settings") or {}).get("breakout_room") or {})
        rooms = breakout.get("rooms") or []
        names: List[str] = []
        for room in rooms:
            if isinstance(room, dict):
                name = room.get("name")
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
        return names


client = ZoomApiClient()
