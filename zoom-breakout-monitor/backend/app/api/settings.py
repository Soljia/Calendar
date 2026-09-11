"""Dashboard settings: alert keywords, notifications, activity thresholds.

Privacy-relevant switches (``DISPLAY_PRIVATE_MESSAGES``, ``CHAT_RETENTION_DAYS``)
are returned read-only. They are deliberately not writable over the API so that
private-message monitoring can never be turned on from a browser session; it
takes an explicit environment change and a restart.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import DashboardSettings, SettingsOut
from ..services.settings_store import load_settings, save_settings, to_settings_out
from ..services.websocket import manager
from .deps import CurrentUser, current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=SettingsOut)
def read_settings(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(current_user),
) -> SettingsOut:
    return to_settings_out(load_settings(db))


@router.put("", response_model=SettingsOut)
async def write_settings(
    payload: DashboardSettings,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(current_user),
) -> SettingsOut:
    saved = save_settings(db, payload)
    out = to_settings_out(saved)
    logger.info("settings_saved", extra={"by": user.username})
    await manager.broadcast({"type": "settings_updated", "data": out.model_dump(mode="json")})
    return out
