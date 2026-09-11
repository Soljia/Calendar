"""Scheduled deletion of expired chat records."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import session_scope
from ..models import BreakoutRoom, ChatMessage, Meeting, utcnow

logger = logging.getLogger(__name__)


def purge_expired_messages(db: Session, retention_days: int | None = None) -> int:
    """Delete messages older than the retention window. 0 == keep forever."""
    days = get_settings().chat_retention_days if retention_days is None else retention_days
    if days <= 0:
        return 0

    cutoff = utcnow() - timedelta(days=days)
    result = db.execute(delete(ChatMessage).where(ChatMessage.received_at < cutoff))
    removed = int(result.rowcount or 0)
    if removed:
        _resync_counters(db)
        logger.info(
            "retention_purge_completed",
            extra={"messages_deleted": removed, "retention_days": days},
        )
    return removed


def _resync_counters(db: Session) -> None:
    """Bring the cached per-room counters back in line after a purge."""
    from sqlalchemy import case, func

    totals = {
        row[0]: (int(row[1] or 0), int(row[2] or 0))
        for row in db.execute(
            select(
                ChatMessage.room_id,
                func.count(ChatMessage.id),
                func.sum(case((ChatMessage.is_alert.is_(True), 1), else_=0)),
            ).group_by(ChatMessage.room_id)
        )
    }
    for room in db.execute(select(BreakoutRoom)).scalars():
        count, alerts = totals.get(room.id, (0, 0))
        room.message_count = count
        room.alert_count = alerts


def mark_stale_meetings_inactive(db: Session, idle_hours: int = 12) -> int:
    """Flag meetings with no traffic for a long while as no longer current."""
    cutoff = utcnow() - timedelta(hours=idle_hours)
    stale = list(
        db.execute(
            select(Meeting).where(Meeting.active.is_(True), Meeting.last_activity < cutoff)
        ).scalars()
    )
    for meeting in stale:
        meeting.active = False
    return len(stale)


async def retention_loop(stop_event: asyncio.Event) -> None:
    """Background sweep. Cancelled on shutdown by the lifespan handler."""
    settings = get_settings()
    interval = max(60, settings.retention_sweep_interval_minutes * 60)

    if settings.chat_retention_days <= 0:
        logger.info("retention_disabled_messages_kept_indefinitely")
    else:
        logger.info(
            "retention_loop_started",
            extra={
                "retention_days": settings.chat_retention_days,
                "interval_minutes": settings.retention_sweep_interval_minutes,
            },
        )

    while not stop_event.is_set():
        try:
            with session_scope() as db:
                purge_expired_messages(db)
                mark_stale_meetings_inactive(db)
        except Exception:  # pragma: no cover - defensive
            logger.exception("retention_sweep_failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
