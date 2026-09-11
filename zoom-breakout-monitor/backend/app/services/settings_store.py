"""Persistence for operator-tunable settings."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..config import DEFAULT_ALERT_KEYWORDS, get_settings
from ..models import AppSetting, utcnow
from ..schemas import DashboardSettings, SettingsOut

logger = logging.getLogger(__name__)

SETTINGS_KEY = "dashboard"


def default_settings() -> DashboardSettings:
    env = get_settings()
    return DashboardSettings(
        alerts_enabled=True,
        alert_keywords=list(DEFAULT_ALERT_KEYWORDS),
        browser_notifications_enabled=False,
        inactivity_warning_minutes=env.inactivity_warning_minutes,
    )


def load_settings(db: Session) -> DashboardSettings:
    row = db.get(AppSetting, SETTINGS_KEY)
    if row is None:
        return default_settings()
    try:
        return DashboardSettings.model_validate(row.value)
    except Exception:
        # A hand-edited or schema-drifted row should not take the app down.
        logger.warning("settings_row_invalid_using_defaults")
        return default_settings()


def save_settings(db: Session, values: DashboardSettings) -> DashboardSettings:
    row = db.get(AppSetting, SETTINGS_KEY)
    payload = values.model_dump(mode="json")
    if row is None:
        row = AppSetting(key=SETTINGS_KEY, value=payload, updated_at=utcnow())
        db.add(row)
    else:
        row.value = payload
        row.updated_at = utcnow()
    db.commit()
    logger.info(
        "settings_updated",
        extra={
            "alerts_enabled": values.alerts_enabled,
            "keyword_count": len(values.alert_keywords),
            "inactivity_warning_minutes": values.inactivity_warning_minutes,
        },
    )
    return values


def ensure_settings(db: Session) -> DashboardSettings:
    """Materialise defaults on first boot so the row is visible to operators."""
    if db.get(AppSetting, SETTINGS_KEY) is None:
        return save_settings(db, default_settings())
    return load_settings(db)


def to_settings_out(values: DashboardSettings) -> SettingsOut:
    env = get_settings()
    return SettingsOut(
        **values.model_dump(),
        display_private_messages=env.display_private_messages,
        chat_retention_days=env.chat_retention_days,
        dev_mode=env.dev_mode,
    )
