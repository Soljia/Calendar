"""ORM entities.

Timestamp convention: every ``DateTime`` column stores a *naive UTC* value.
SQLite has no timezone-aware storage, so normalising on write and re-attaching
UTC on serialisation is what keeps SQLite and PostgreSQL behaving identically.
Use :func:`utcnow` everywhere instead of ``datetime.utcnow``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from ..database import Base

# Sentinel stored in ``BreakoutRoom.breakout_room_uuid`` for messages that were
# sent in the main meeting rather than a breakout room. An empty string (rather
# than NULL) keeps the unique constraint meaningful on every backend.
MAIN_ROOM_SENTINEL = ""


def utcnow() -> datetime:
    """Current time as a naive UTC datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_utc(value: datetime | None) -> datetime | None:
    """Re-attach UTC to a naive value read back from the database."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    zoom_meeting_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    zoom_meeting_uuid: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    topic: Mapped[str | None] = mapped_column(String(512), nullable=True)
    host_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_activity: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    rooms: Mapped[list["BreakoutRoom"]] = relationship(
        back_populates="meeting",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="meeting",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (Index("ix_meetings_last_activity", "last_activity"),)

    @property
    def display_topic(self) -> str:
        return self.topic or f"Meeting {self.zoom_meeting_id or self.zoom_meeting_uuid}"


class BreakoutRoom(Base):
    __tablename__ = "breakout_rooms"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    meeting_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    meeting_uuid: Mapped[str] = mapped_column(String(128), nullable=False)
    # MAIN_ROOM_SENTINEL ("") when the message came from the main meeting.
    breakout_room_uuid: Mapped[str] = mapped_column(String(128), nullable=False)
    # Stable per-meeting ordinal. 0 is reserved for the main meeting.
    room_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Set when an administrator renames the room, or when a name is recovered
    # from the Zoom API. Overrides the generated "Breakout Room N" label.
    custom_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_main_room: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_activity: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    alert_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    meeting: Mapped[Meeting] = relationship(back_populates="rooms")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="room",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("meeting_id", "breakout_room_uuid", name="uq_room_per_meeting"),
        Index("ix_rooms_meeting_uuid", "meeting_uuid"),
    )

    @property
    def display_name(self) -> str:
        if self.custom_name:
            return self.custom_name
        if self.is_main_room:
            return "Main Meeting"
        return f"Breakout Room {self.room_number}"


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    meeting_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    room_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("breakout_rooms.id", ondelete="CASCADE"), nullable=False
    )
    meeting_uuid: Mapped[str] = mapped_column(String(128), nullable=False)
    zoom_meeting_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    breakout_room_uuid: Mapped[str] = mapped_column(String(128), nullable=False)

    # Zoom's own message id when it supplies one; used to drop duplicate
    # deliveries (Zoom retries a webhook that did not return 2xx).
    zoom_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    sender_name: Mapped[str] = mapped_column(String(255), nullable=False, default="Unknown")
    sender_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sender_context: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recipient_context: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recipient_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recipient_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    is_private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_alert: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    matched_keywords: Mapped[list | None] = mapped_column(JSON, nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Audit copy of the delivery Zoom sent. Never exposed through the normal
    # API surface -- see app/api/messages.py.
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    meeting: Mapped[Meeting] = relationship(back_populates="messages")
    room: Mapped[BreakoutRoom] = relationship(back_populates="messages")

    __table_args__ = (
        Index("ix_messages_meeting_ts", "meeting_uuid", "timestamp"),
        Index("ix_messages_room_ts", "room_id", "timestamp"),
        Index("ix_messages_received_at", "received_at"),
        Index("ix_messages_zoom_id", "meeting_uuid", "zoom_message_id"),
    )


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Bumping this invalidates every already-issued session token.
    token_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class AppSetting(Base):
    """Single-row-per-key store for operator-tunable settings."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
