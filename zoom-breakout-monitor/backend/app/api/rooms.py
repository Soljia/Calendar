"""Breakout room administration."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import BreakoutRoom
from ..schemas import RoomOut, RoomUpdate
from ..services.serializers import room_to_out
from ..services.websocket import manager
from .deps import CurrentUser, current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


@router.get("/{room_id}", response_model=RoomOut)
def get_room(
    room_id: str,
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(current_user),
    include_messages: bool = True,
) -> RoomOut:
    room = db.get(BreakoutRoom, room_id)
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
    return room_to_out(db, room, include_messages=include_messages)


@router.patch("/{room_id}", response_model=RoomOut)
async def update_room(
    room_id: str,
    payload: RoomUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(current_user),
) -> RoomOut:
    """Rename a room. Send ``custom_name: null`` to restore the generated name."""
    room = db.get(BreakoutRoom, room_id)
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    room.custom_name = payload.custom_name
    db.commit()
    db.refresh(room)

    logger.info(
        "room_renamed",
        extra={
            "meeting_uuid": room.meeting_uuid,
            "room_number": room.room_number,
            "named": bool(payload.custom_name),
            "by": user.username,
        },
    )

    out = room_to_out(db, room, include_messages=False)
    await manager.broadcast(
        {"type": "room_updated", "data": out.model_dump(mode="json")},
        channel=room.meeting_uuid,
    )
    return out
