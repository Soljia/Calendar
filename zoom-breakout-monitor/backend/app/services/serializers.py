"""ORM -> API model conversion.

Counts exposed to the dashboard are computed here with the current privacy
policy applied, so a deployment that hides private messages never reports a
number that includes them.
"""

from __future__ import annotations

from typing import Iterable, List, Sequence

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import BreakoutRoom, ChatMessage, Meeting, as_utc
from ..schemas import MeetingDetail, MeetingOut, MessageOut, RoomOut

# Boolean SUM() differs between SQLite and PostgreSQL; a CASE keeps it portable.
_ALERT_SUM = func.sum(case((ChatMessage.is_alert.is_(True), 1), else_=0))


def private_messages_visible() -> bool:
    return get_settings().display_private_messages


def visibility_clause():
    """SQLAlchemy clause restricting a query to displayable messages."""
    if private_messages_visible():
        return None
    return ChatMessage.is_private.is_(False)


def apply_visibility(statement):
    clause = visibility_clause()
    return statement if clause is None else statement.where(clause)


def message_to_out(message: ChatMessage, room: BreakoutRoom | None = None) -> MessageOut:
    room = room or message.room
    keywords = message.matched_keywords or []
    return MessageOut(
        id=message.id,
        meeting_uuid=message.meeting_uuid,
        zoom_meeting_id=message.zoom_meeting_id,
        room_id=message.room_id,
        room_name=room.display_name if room else "Unknown",
        room_number=room.room_number if room else 0,
        breakout_room_uuid=message.breakout_room_uuid or None,
        sender_name=message.sender_name,
        sender_id=message.sender_id,
        message=message.message,
        timestamp=as_utc(message.timestamp),
        sender_context=message.sender_context,
        recipient_context=message.recipient_context,
        recipient_type=message.recipient_type,
        is_private=message.is_private,
        is_alert=message.is_alert,
        matched_keywords=list(keywords),
        simulated=message.simulated,
    )


def _room_counts(db: Session, room_ids: Sequence[str]) -> dict[str, tuple[int, int]]:
    if not room_ids:
        return {}
    statement = apply_visibility(
        select(
            ChatMessage.room_id,
            func.count(ChatMessage.id),
            _ALERT_SUM,
        ).where(ChatMessage.room_id.in_(room_ids))
    ).group_by(ChatMessage.room_id)
    return {row[0]: (int(row[1] or 0), int(row[2] or 0)) for row in db.execute(statement)}


def recent_messages(db: Session, room_id: str, limit: int) -> List[ChatMessage]:
    statement = (
        apply_visibility(select(ChatMessage).where(ChatMessage.room_id == room_id))
        .order_by(ChatMessage.timestamp.desc(), ChatMessage.received_at.desc())
        .limit(limit)
    )
    rows = list(db.execute(statement).scalars())
    rows.reverse()  # oldest first, so the UI can append downwards
    return rows


def room_to_out(
    db: Session,
    room: BreakoutRoom,
    *,
    include_messages: bool = False,
    counts: tuple[int, int] | None = None,
) -> RoomOut:
    if counts is None:
        counts = _room_counts(db, [room.id]).get(room.id, (0, 0))
    message_count, alert_count = counts

    messages: List[MessageOut] = []
    if include_messages:
        limit = get_settings().room_recent_messages
        messages = [message_to_out(m, room) for m in recent_messages(db, room.id, limit)]

    return RoomOut(
        id=room.id,
        meeting_uuid=room.meeting_uuid,
        breakout_room_uuid=room.breakout_room_uuid or None,
        name=room.display_name,
        room_number=room.room_number,
        is_main_room=room.is_main_room,
        custom_name=room.custom_name,
        first_seen=as_utc(room.first_seen),
        last_activity=as_utc(room.last_activity),
        message_count=message_count,
        alert_count=alert_count,
        recent_messages=messages,
    )


def rooms_to_out(
    db: Session,
    rooms: Iterable[BreakoutRoom],
    *,
    include_messages: bool = False,
) -> List[RoomOut]:
    rooms = list(rooms)
    counts = _room_counts(db, [room.id for room in rooms])
    return [
        room_to_out(db, room, include_messages=include_messages, counts=counts.get(room.id, (0, 0)))
        for room in rooms
    ]


def _meeting_totals(db: Session, meeting_id: str) -> tuple[int, int, int]:
    room_count = db.execute(
        select(func.count(BreakoutRoom.id)).where(BreakoutRoom.meeting_id == meeting_id)
    ).scalar_one()
    totals = db.execute(
        apply_visibility(
            select(func.count(ChatMessage.id), _ALERT_SUM).where(
                ChatMessage.meeting_id == meeting_id
            )
        )
    ).one()
    return int(room_count or 0), int(totals[0] or 0), int(totals[1] or 0)


def meeting_to_out(db: Session, meeting: Meeting) -> MeetingOut:
    room_count, message_count, alert_count = _meeting_totals(db, meeting.id)
    return MeetingOut(
        id=meeting.id,
        zoom_meeting_id=meeting.zoom_meeting_id,
        meeting_uuid=meeting.zoom_meeting_uuid,
        topic=meeting.display_topic,
        start_time=as_utc(meeting.start_time),
        last_activity=as_utc(meeting.last_activity),
        active=meeting.active,
        simulated=meeting.simulated,
        room_count=room_count,
        message_count=message_count,
        alert_count=alert_count,
    )


def meeting_to_detail(db: Session, meeting: Meeting, *, include_messages: bool = True) -> MeetingDetail:
    base = meeting_to_out(db, meeting)
    rooms = list(
        db.execute(
            select(BreakoutRoom)
            .where(BreakoutRoom.meeting_id == meeting.id)
            .order_by(BreakoutRoom.room_number.asc(), BreakoutRoom.first_seen.asc())
        ).scalars()
    )
    return MeetingDetail(
        **base.model_dump(),
        rooms=rooms_to_out(db, rooms, include_messages=include_messages),
    )
