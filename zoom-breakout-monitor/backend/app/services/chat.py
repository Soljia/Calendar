"""Chat ingestion: normalise -> group by breakout room -> persist -> notify.

This module owns the privacy rules. A private message is persisted as metadata
only (no body, no raw payload, no keyword matches) unless the deployment has
explicitly set ``DISPLAY_PRIVATE_MESSAGES=true``, and it is never broadcast
while it is not displayable.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..logging_config import redact
from ..models import MAIN_ROOM_SENTINEL, BreakoutRoom, ChatMessage, Meeting, utcnow
from ..schemas import DashboardSettings
from .serializers import message_to_out, room_to_out
from .settings_store import load_settings
from .zoom import UNKNOWN_BREAKOUT_SENTINEL, NormalizedChatMessage

logger = logging.getLogger(__name__)

# Serialises room-number allocation within this process. The unique constraint
# on (meeting_id, breakout_room_uuid) is the real backstop.
_allocation_lock = threading.Lock()

_KEYWORD_CACHE: dict[tuple[str, ...], List[tuple[str, re.Pattern[str]]]] = {}


def _to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Keyword alerts
# ---------------------------------------------------------------------------


def _compiled_keywords(keywords: Sequence[str]) -> List[tuple[str, re.Pattern[str]]]:
    key = tuple(keywords)
    cached = _KEYWORD_CACHE.get(key)
    if cached is None:
        cached = [
            # Anchored at a word start only, so "help" matches "helping" and
            # "Help!" but not "whelp". Case-insensitive by request.
            (kw, re.compile(r"\b" + re.escape(kw), re.IGNORECASE))
            for kw in keywords
            if kw
        ]
        if len(_KEYWORD_CACHE) > 32:
            _KEYWORD_CACHE.clear()
        _KEYWORD_CACHE[key] = cached
    return cached


def match_keywords(text: str, keywords: Sequence[str]) -> List[str]:
    """Return the keywords present in ``text`` (case-insensitive)."""
    if not text or not keywords:
        return []
    return [kw for kw, pattern in _compiled_keywords(tuple(keywords)) if pattern.search(text)]


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------


def get_or_create_meeting(
    db: Session, normalized: NormalizedChatMessage, *, simulated: bool = False
) -> tuple[Meeting, bool]:
    meeting = db.execute(
        select(Meeting).where(Meeting.zoom_meeting_uuid == normalized.meeting_uuid)
    ).scalar_one_or_none()
    if meeting is not None:
        if normalized.meeting_topic and meeting.topic != normalized.meeting_topic:
            meeting.topic = normalized.meeting_topic
        if normalized.zoom_meeting_id and not meeting.zoom_meeting_id:
            meeting.zoom_meeting_id = normalized.zoom_meeting_id
        return meeting, False

    start_time = normalized.meeting_start_time or normalized.timestamp
    meeting = Meeting(
        zoom_meeting_id=normalized.zoom_meeting_id,
        zoom_meeting_uuid=normalized.meeting_uuid,
        topic=normalized.meeting_topic,
        host_id=normalized.host_id,
        start_time=_to_naive_utc(start_time),
        last_activity=_to_naive_utc(normalized.timestamp),
        active=True,
        simulated=simulated,
    )
    db.add(meeting)
    try:
        db.flush()
    except IntegrityError:
        # Concurrent delivery beat us to it.
        db.rollback()
        meeting = db.execute(
            select(Meeting).where(Meeting.zoom_meeting_uuid == normalized.meeting_uuid)
        ).scalar_one()
        return meeting, False

    logger.info(
        "new_meeting_detected",
        extra={
            "meeting_uuid": meeting.zoom_meeting_uuid,
            "zoom_meeting_id": meeting.zoom_meeting_id,
            "simulated": simulated,
        },
    )
    return meeting, True


def _next_room_number(db: Session, meeting_id: str) -> int:
    numbers = db.execute(
        select(BreakoutRoom.room_number).where(
            BreakoutRoom.meeting_id == meeting_id,
            BreakoutRoom.is_main_room.is_(False),
        )
    ).scalars()
    highest = max((n for n in numbers), default=0)
    return highest + 1


def get_or_create_room(
    db: Session, meeting: Meeting, breakout_room_uuid: Optional[str]
) -> tuple[BreakoutRoom, bool]:
    """Resolve the room a message belongs to.

    ``breakout_room_uuid`` of ``None``/empty means the main meeting, which gets
    a reserved room with number 0. Real breakout rooms are numbered from 1 in
    order of first appearance, and that mapping is stable for the meeting's
    lifetime because it is stored on the row.
    """
    key = (breakout_room_uuid or MAIN_ROOM_SENTINEL).strip()
    is_main = key == MAIN_ROOM_SENTINEL

    room = db.execute(
        select(BreakoutRoom).where(
            BreakoutRoom.meeting_id == meeting.id,
            BreakoutRoom.breakout_room_uuid == key,
        )
    ).scalar_one_or_none()
    if room is not None:
        return room, False

    with _allocation_lock:
        # Re-check inside the lock: another thread may have created it.
        room = db.execute(
            select(BreakoutRoom).where(
                BreakoutRoom.meeting_id == meeting.id,
                BreakoutRoom.breakout_room_uuid == key,
            )
        ).scalar_one_or_none()
        if room is not None:
            return room, False

        room = BreakoutRoom(
            meeting_id=meeting.id,
            meeting_uuid=meeting.zoom_meeting_uuid,
            breakout_room_uuid=key,
            room_number=0 if is_main else _next_room_number(db, meeting.id),
            is_main_room=is_main,
            first_seen=utcnow(),
            last_activity=utcnow(),
        )
        if key == UNKNOWN_BREAKOUT_SENTINEL:
            # Zoom told us "breakout" without a room id; label it honestly.
            room.custom_name = "Breakout Room (unidentified)"
        db.add(room)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            return (
                db.execute(
                    select(BreakoutRoom).where(
                        BreakoutRoom.meeting_id == meeting.id,
                        BreakoutRoom.breakout_room_uuid == key,
                    )
                ).scalar_one(),
                False,
            )

    logger.info(
        "new_breakout_room_detected",
        extra={
            "meeting_uuid": meeting.zoom_meeting_uuid,
            "room_number": room.room_number,
            "is_main_room": room.is_main_room,
            "breakout_room_uuid": key or None,
        },
    )
    return room, True


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


@dataclass
class IngestOutcome:
    """Result of persisting one chat delivery, ready to broadcast."""

    stored: bool = False
    duplicate: bool = False
    visible: bool = False
    meeting_uuid: str = ""
    events: List[Dict[str, Any]] = field(default_factory=list)


def _find_duplicate(
    db: Session, meeting_id: str, zoom_message_id: Optional[str]
) -> ChatMessage | None:
    if not zoom_message_id:
        return None
    return db.execute(
        select(ChatMessage).where(
            ChatMessage.meeting_id == meeting_id,
            ChatMessage.zoom_message_id == zoom_message_id,
        )
    ).scalar_one_or_none()


def ingest_chat_message(
    db: Session,
    normalized: NormalizedChatMessage,
    *,
    simulated: bool = False,
    settings: DashboardSettings | None = None,
) -> IngestOutcome:
    """Persist one normalised message and describe what changed.

    The caller is responsible for committing (``session_scope`` does) and for
    pushing ``outcome.events`` onto the WebSocket fan-out.
    """
    env = get_settings()
    dashboard = settings if settings is not None else load_settings(db)

    meeting, meeting_created = get_or_create_meeting(db, normalized, simulated=simulated)
    room, room_created = get_or_create_room(db, meeting, normalized.breakout_room_uuid)

    if _find_duplicate(db, meeting.id, normalized.zoom_message_id) is not None:
        logger.debug(
            "duplicate_chat_message_ignored",
            extra={
                "meeting_uuid": meeting.zoom_meeting_uuid,
                "zoom_message_id": normalized.zoom_message_id,
            },
        )
        return IngestOutcome(
            stored=False,
            duplicate=True,
            meeting_uuid=meeting.zoom_meeting_uuid,
        )

    # --- privacy gate ---------------------------------------------------
    visible = (not normalized.is_private) or env.display_private_messages
    body = normalized.message if visible else ""
    matched = (
        match_keywords(body, dashboard.alert_keywords)
        if (visible and dashboard.alerts_enabled)
        else []
    )
    keep_raw = env.store_raw_webhook_payload and visible and bool(normalized.raw_payload)

    message = ChatMessage(
        meeting_id=meeting.id,
        room_id=room.id,
        meeting_uuid=meeting.zoom_meeting_uuid,
        zoom_meeting_id=normalized.zoom_meeting_id or meeting.zoom_meeting_id,
        breakout_room_uuid=room.breakout_room_uuid,
        zoom_message_id=normalized.zoom_message_id,
        sender_name=normalized.sender_name or "Unknown",
        sender_id=normalized.sender_id,
        sender_context=normalized.sender_context,
        recipient_context=normalized.recipient_context,
        recipient_type=normalized.recipient_type,
        recipient_name=normalized.recipient_name if visible else None,
        recipient_id=normalized.recipient_id if visible else None,
        message=body,
        timestamp=_to_naive_utc(normalized.timestamp),
        received_at=utcnow(),
        is_private=normalized.is_private,
        is_alert=bool(matched),
        matched_keywords=matched or None,
        simulated=simulated,
        raw_payload=dict(normalized.raw_payload) if keep_raw else None,
    )
    db.add(message)

    now = utcnow()
    room.last_activity = now
    meeting.last_activity = now
    meeting.active = True
    if visible:
        room.message_count += 1
        if message.is_alert:
            room.alert_count += 1

    db.flush()

    logger.info(
        "chat_message_stored",
        extra={
            "meeting_uuid": meeting.zoom_meeting_uuid,
            "room_number": room.room_number,
            "is_private": normalized.is_private,
            "displayed": visible,
            "is_alert": message.is_alert,
            "simulated": simulated,
        },
    )
    if visible:
        logger.debug(
            "chat_message_content",
            extra={"room_number": room.room_number, "preview": redact(body)},
        )

    outcome = IngestOutcome(
        stored=True,
        visible=visible,
        meeting_uuid=meeting.zoom_meeting_uuid,
    )

    # Serialise inside the session: callers broadcast after the session closes.
    if meeting_created:
        from .serializers import meeting_to_out

        outcome.events.append(
            {"type": "meeting_created", "data": meeting_to_out(db, meeting).model_dump(mode="json")}
        )
    if room_created:
        outcome.events.append(
            {
                "type": "room_created",
                "data": room_to_out(db, room, include_messages=False).model_dump(mode="json"),
            }
        )
    if visible:
        room_payload = room_to_out(db, room, include_messages=False).model_dump(mode="json")
        outcome.events.append(
            {
                "type": "chat_message",
                "data": {
                    "room": room_payload,
                    "message": message_to_out(message, room).model_dump(mode="json"),
                },
            }
        )

    return outcome
