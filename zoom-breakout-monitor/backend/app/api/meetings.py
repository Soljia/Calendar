"""Meetings, rooms and the combined message feed."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..database import get_db
from ..models import BreakoutRoom, ChatMessage, Meeting
from ..schemas import (
    DeleteResult,
    MeetingDetail,
    MeetingOut,
    MessageOut,
    MessagePage,
    RoomNameSuggestions,
    RoomOut,
)
from ..services.serializers import apply_visibility, message_to_out, meeting_to_detail, meeting_to_out, rooms_to_out
from ..services.websocket import manager
from ..services.zoom_api import ZoomApiError, client as zoom_client
from .deps import CurrentUser, current_user, settings_dep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/meetings", tags=["meetings"])


def resolve_meeting(db: Session, identifier: str) -> Meeting | None:
    """Look a meeting up by internal id or by Zoom meeting UUID.

    Zoom meeting UUIDs are base64 and routinely contain ``/``, ``+`` and ``=``,
    which do not survive a URL path cleanly (ASGI servers percent-decode the
    path before routing, so an encoded slash reappears as a path separator).
    Every route therefore accepts either form, and the frontend uses the
    internal id, which is hex and always URL-safe.
    """
    meeting = db.get(Meeting, identifier)
    if meeting is not None:
        return meeting

    for candidate in (identifier, unquote(identifier)):
        meeting = db.execute(
            select(Meeting).where(Meeting.zoom_meeting_uuid == candidate)
        ).scalar_one_or_none()
        if meeting is not None:
            return meeting
    return None


def _load_meeting(db: Session, meeting_uuid: str) -> Meeting:
    meeting = resolve_meeting(db, meeting_uuid)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    return meeting


@router.get("", response_model=List[MeetingOut])
def list_meetings(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(current_user),
    active: Optional[bool] = Query(default=None, description="Filter by active state"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> List[MeetingOut]:
    statement = select(Meeting)
    if active is not None:
        statement = statement.where(Meeting.active.is_(active))
    statement = statement.order_by(Meeting.last_activity.desc()).limit(limit).offset(offset)
    return [meeting_to_out(db, m) for m in db.execute(statement).scalars()]


@router.get("/current", response_model=Optional[MeetingOut])
def current_meeting(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(current_user),
) -> Optional[MeetingOut]:
    """The most recently active meeting, or ``null`` when there is none."""
    meeting = db.execute(
        select(Meeting).order_by(Meeting.active.desc(), Meeting.last_activity.desc()).limit(1)
    ).scalar_one_or_none()
    return meeting_to_out(db, meeting) if meeting else None


@router.get("/{meeting_uuid}/rooms", response_model=List[RoomOut])
def list_rooms(
    meeting_uuid: str,
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(current_user),
    include_messages: bool = Query(default=True),
) -> List[RoomOut]:
    meeting = _load_meeting(db, meeting_uuid)
    rooms = db.execute(
        select(BreakoutRoom)
        .where(BreakoutRoom.meeting_id == meeting.id)
        .order_by(BreakoutRoom.room_number.asc(), BreakoutRoom.first_seen.asc())
    ).scalars()
    return rooms_to_out(db, rooms, include_messages=include_messages)


@router.get("/{meeting_uuid}/messages", response_model=MessagePage)
def list_messages(
    meeting_uuid: str,
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(settings_dep),
    room_id: Optional[str] = Query(default=None),
    sender: Optional[str] = Query(default=None, max_length=255),
    q: Optional[str] = Query(default=None, max_length=500, description="Message content search"),
    alerts_only: bool = Query(default=False),
    since: Optional[datetime] = Query(default=None),
    until: Optional[datetime] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> MessagePage:
    """Combined, filterable chronological feed for one meeting."""
    meeting = _load_meeting(db, meeting_uuid)

    filters = [ChatMessage.meeting_id == meeting.id]
    if room_id:
        filters.append(ChatMessage.room_id == room_id)
    if sender:
        filters.append(ChatMessage.sender_name.ilike(f"%{sender}%"))
    if q:
        # Parameterised by SQLAlchemy; the value is never interpolated into SQL.
        filters.append(
            or_(ChatMessage.message.ilike(f"%{q}%"), ChatMessage.sender_name.ilike(f"%{q}%"))
        )
    if alerts_only:
        filters.append(ChatMessage.is_alert.is_(True))
    if since:
        filters.append(ChatMessage.timestamp >= since.replace(tzinfo=None))
    if until:
        filters.append(ChatMessage.timestamp <= until.replace(tzinfo=None))

    limit = min(limit, settings.messages_page_size * 5)

    total = db.execute(
        apply_visibility(select(func.count(ChatMessage.id)).where(*filters))
    ).scalar_one()

    rows = db.execute(
        apply_visibility(select(ChatMessage).where(*filters))
        .order_by(ChatMessage.timestamp.desc(), ChatMessage.received_at.desc())
        .limit(limit)
        .offset(offset)
    ).scalars()

    rooms = {
        room.id: room
        for room in db.execute(
            select(BreakoutRoom).where(BreakoutRoom.meeting_id == meeting.id)
        ).scalars()
    }
    items: List[MessageOut] = [message_to_out(m, rooms.get(m.room_id)) for m in rows]
    items.reverse()  # chronological ascending for display
    return MessagePage(items=items, total=int(total or 0), limit=limit, offset=offset)


@router.get("/{meeting_uuid}/room-name-suggestions", response_model=RoomNameSuggestions)
async def room_name_suggestions(
    meeting_uuid: str,
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(current_user),
) -> RoomNameSuggestions:
    """Pre-assigned breakout-room names from the Zoom Meeting API, if available.

    Zoom's chat webhook carries a breakout-room UUID but no name, and there is
    no public UUID -> name mapping, so these are suggestions an administrator
    applies by hand. The monitor is fully usable without them.
    """
    meeting = _load_meeting(db, meeting_uuid)
    if not zoom_client.configured:
        return RoomNameSuggestions(
            available=False,
            reason="Zoom Server-to-Server OAuth credentials are not configured.",
        )
    if not meeting.zoom_meeting_id:
        return RoomNameSuggestions(
            available=False, reason="No numeric Zoom meeting ID is known for this meeting."
        )
    try:
        names = await zoom_client.preassigned_room_names(meeting.zoom_meeting_id)
    except ZoomApiError as exc:
        return RoomNameSuggestions(available=False, reason=str(exc))
    if not names:
        return RoomNameSuggestions(
            available=False,
            reason="Zoom reports no pre-assigned breakout rooms for this meeting.",
        )
    return RoomNameSuggestions(available=True, names=names)


@router.delete("/{meeting_uuid}", response_model=DeleteResult)
async def delete_meeting(
    meeting_uuid: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(current_user),
    confirm: bool = Query(
        default=False, description="Must be true; guards against accidental deletion"
    ),
) -> DeleteResult:
    """Permanently delete a meeting and every room and message under it."""
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deletion requires confirm=true",
        )
    meeting = _load_meeting(db, meeting_uuid)
    zoom_uuid = meeting.zoom_meeting_uuid

    message_count = db.execute(
        select(func.count(ChatMessage.id)).where(ChatMessage.meeting_id == meeting.id)
    ).scalar_one()
    room_count = db.execute(
        select(func.count(BreakoutRoom.id)).where(BreakoutRoom.meeting_id == meeting.id)
    ).scalar_one()

    # Explicit child deletes keep the behaviour identical on backends where
    # ON DELETE CASCADE is not enforced.
    db.execute(delete(ChatMessage).where(ChatMessage.meeting_id == meeting.id))
    db.execute(delete(BreakoutRoom).where(BreakoutRoom.meeting_id == meeting.id))
    db.execute(delete(Meeting).where(Meeting.id == meeting.id))
    db.commit()

    logger.info(
        "meeting_deleted",
        extra={
            "meeting_uuid": zoom_uuid,
            "rooms_deleted": int(room_count or 0),
            "messages_deleted": int(message_count or 0),
            "by": user.username,
        },
    )
    await manager.broadcast(
        {"type": "meeting_deleted", "data": {"meeting_uuid": zoom_uuid, "id": meeting.id}},
        channel=zoom_uuid,
    )
    return DeleteResult(
        deleted=True,
        meeting_uuid=zoom_uuid,
        rooms_deleted=int(room_count or 0),
        messages_deleted=int(message_count or 0),
    )


@router.get("/{meeting_uuid}", response_model=MeetingDetail)
def get_meeting(
    meeting_uuid: str,
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(current_user),
    include_messages: bool = Query(default=True),
) -> MeetingDetail:
    meeting = _load_meeting(db, meeting_uuid)
    return meeting_to_detail(db, meeting, include_messages=include_messages)
