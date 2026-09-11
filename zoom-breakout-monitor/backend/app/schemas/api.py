"""Pydantic models for the REST/WebSocket surface.

These are the contract the frontend codes against -- deliberately decoupled
from both the ORM rows and Zoom's raw webhook JSON.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_KEYWORDS = 100
MAX_KEYWORD_LENGTH = 64


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class SessionInfo(BaseModel):
    authenticated: bool
    username: Optional[str] = None
    expires_at: Optional[datetime] = None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    meeting_uuid: str
    zoom_meeting_id: Optional[str] = None
    room_id: str
    room_name: str
    room_number: int
    breakout_room_uuid: Optional[str] = None
    sender_name: str
    sender_id: Optional[str] = None
    message: str
    timestamp: datetime
    sender_context: Optional[str] = None
    recipient_context: Optional[str] = None
    recipient_type: Optional[str] = None
    is_private: bool = False
    is_alert: bool = False
    matched_keywords: List[str] = Field(default_factory=list)
    simulated: bool = False


class RoomOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    meeting_uuid: str
    breakout_room_uuid: Optional[str] = None
    name: str
    room_number: int
    is_main_room: bool
    custom_name: Optional[str] = None
    first_seen: datetime
    last_activity: datetime
    message_count: int
    alert_count: int
    recent_messages: List[MessageOut] = Field(default_factory=list)


class MeetingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    zoom_meeting_id: Optional[str] = None
    meeting_uuid: str
    topic: str
    start_time: datetime
    last_activity: datetime
    active: bool
    simulated: bool
    room_count: int = 0
    message_count: int = 0
    alert_count: int = 0


class MeetingDetail(MeetingOut):
    rooms: List[RoomOut] = Field(default_factory=list)


class MessagePage(BaseModel):
    items: List[MessageOut]
    total: int
    limit: int
    offset: int


class RoomUpdate(BaseModel):
    """Rename a room. ``null`` restores the generated name."""

    custom_name: Optional[str] = Field(default=None, max_length=120)

    @field_validator("custom_name")
    @classmethod
    def _clean(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


class DashboardSettings(BaseModel):
    """Operator-tunable settings, persisted in the database."""

    alerts_enabled: bool = True
    alert_keywords: List[str] = Field(default_factory=list)
    browser_notifications_enabled: bool = False
    inactivity_warning_minutes: int = Field(default=5, ge=1, le=240)

    @field_validator("alert_keywords")
    @classmethod
    def _clean_keywords(cls, value: List[str]) -> List[str]:
        seen: set[str] = set()
        cleaned: List[str] = []
        for raw in value:
            keyword = str(raw).strip().lower()
            if not keyword or len(keyword) > MAX_KEYWORD_LENGTH:
                continue
            if keyword in seen:
                continue
            seen.add(keyword)
            cleaned.append(keyword)
        return cleaned[:MAX_KEYWORDS]


class SettingsOut(DashboardSettings):
    """Settings plus the read-only, environment-controlled policy values.

    ``display_private_messages`` and ``chat_retention_days`` are intentionally
    *not* writable through the API: flipping privacy behaviour is a deployment
    decision, so it takes an explicit environment change and a restart.
    """

    display_private_messages: bool
    chat_retention_days: int
    dev_mode: bool


class DeleteResult(BaseModel):
    deleted: bool
    meeting_uuid: str
    rooms_deleted: int
    messages_deleted: int


class SimulationRequest(BaseModel):
    topic: str = Field(default="Simulated Class", max_length=200)
    rooms: int = Field(default=4, ge=1, le=50)
    participants_per_room: int = Field(default=3, ge=1, le=20)
    messages: int = Field(default=12, ge=0, le=500)
    interval_seconds: float = Field(default=2.0, ge=0.1, le=60.0)
    include_alerts: bool = True
    meeting_uuid: Optional[str] = Field(default=None, max_length=128)


class SimulationStatus(BaseModel):
    running: bool
    meeting_uuid: Optional[str] = None
    topic: Optional[str] = None
    rooms: int = 0
    messages_sent: int = 0
    messages_planned: int = 0


class RoomNameSuggestions(BaseModel):
    """Pre-assigned breakout-room names read from the Zoom Meeting API.

    Zoom's chat webhook does not carry a human-readable room name, and there is
    no public mapping from ``breakout_room_uuid`` to a configured room. These
    are therefore *suggestions* an administrator can apply manually.
    """

    available: bool
    reason: Optional[str] = None
    names: List[str] = Field(default_factory=list)


class WebSocketEvent(BaseModel):
    type: Literal[
        "hello",
        "chat_message",
        "room_created",
        "room_updated",
        "meeting_created",
        "meeting_updated",
        "meeting_deleted",
        "settings_updated",
        "simulation_status",
        "pong",
    ]
    data: Dict[str, Any] = Field(default_factory=dict)
