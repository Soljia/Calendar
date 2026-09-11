"""WebSocket endpoints.

Authentication is the same HttpOnly session cookie the REST API uses: browsers
send it automatically on a same-origin upgrade, and the shipped deployment puts
the UI and the API behind one origin precisely so this works without ever
exposing the token to page scripts.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from ..config import get_settings
from ..database import SessionLocal
from ..services.websocket import GLOBAL_CHANNEL, manager
from .deps import authenticate_websocket_cookie
from .meetings import resolve_meeting

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

POLICY_VIOLATION = status.WS_1008_POLICY_VIOLATION


def _authenticate(websocket: WebSocket):
    token = websocket.cookies.get(get_settings().cookie_name)
    db = SessionLocal()
    try:
        return authenticate_websocket_cookie(db, token)
    finally:
        db.close()


def _resolve_channel(meeting_identifier: str) -> tuple[str, str] | None:
    """Map a path identifier to (channel, zoom_meeting_uuid)."""
    db = SessionLocal()
    try:
        meeting = resolve_meeting(db, meeting_identifier)
        if meeting is None:
            return None
        return meeting.zoom_meeting_uuid, meeting.zoom_meeting_uuid
    finally:
        db.close()


async def _serve(websocket: WebSocket, channel: str, hello: dict) -> None:
    await manager.connect(websocket, channel)
    try:
        await manager.send_personal(websocket, {"type": "hello", "data": hello})
        while True:
            # The client sends periodic "ping" keepalives; anything else is
            # ignored. The socket is push-only by design.
            message = await websocket.receive_text()
            if message == "ping":
                await manager.send_personal(websocket, {"type": "pong", "data": {}})
    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover - transport failure
        logger.debug("websocket_error", exc_info=True)
    finally:
        await manager.disconnect(websocket, channel)


@router.websocket("/ws")
async def dashboard_socket(websocket: WebSocket) -> None:
    """Dashboard-wide feed: every meeting's events."""
    user = _authenticate(websocket)
    if user is None:
        await websocket.close(code=POLICY_VIOLATION, reason="Authentication required")
        logger.warning("websocket_rejected_unauthenticated", extra={"channel": GLOBAL_CHANNEL})
        return
    await _serve(websocket, GLOBAL_CHANNEL, {"scope": "dashboard", "user": user.username})


@router.websocket("/ws/meetings/{meeting_uuid}")
async def meeting_socket(websocket: WebSocket, meeting_uuid: str) -> None:
    """Per-meeting feed. Accepts the internal meeting id or the Zoom UUID."""
    user = _authenticate(websocket)
    if user is None:
        await websocket.close(code=POLICY_VIOLATION, reason="Authentication required")
        logger.warning("websocket_rejected_unauthenticated", extra={"channel": meeting_uuid})
        return

    resolved = _resolve_channel(meeting_uuid)
    if resolved is None:
        await websocket.close(code=POLICY_VIOLATION, reason="Unknown meeting")
        return

    channel, zoom_uuid = resolved
    await _serve(
        websocket,
        channel,
        {"scope": "meeting", "meeting_uuid": zoom_uuid, "user": user.username},
    )
