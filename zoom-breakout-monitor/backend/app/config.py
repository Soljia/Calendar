"""Application configuration.

Every value is sourced from environment variables (or a local ``.env`` file).
Nothing security-relevant is ever hard-coded: secrets that are missing either
raise at startup or fall back to a random per-process value that is clearly
logged as ephemeral.
"""

from __future__ import annotations

import json
import secrets
from functools import lru_cache
from typing import Annotated, List, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_ALERT_KEYWORDS: List[str] = [
    "help",
    "teacher",
    "question",
    "stuck",
    "confused",
    "problem",
]


def _split_csv(value: object) -> object:
    """Accept a JSON list, a comma-separated string, or an empty value.

    Automatic JSON decoding is turned off for these fields (see NoDecode
    below), so both forms are handled here instead.
    """
    if not isinstance(value, str):
        return value

    stripped = value.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            # Fall through and treat it as a plain comma-separated string
            # rather than failing to start over a malformed bracket.
            pass
    return [item.strip() for item in stripped.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ----- general -------------------------------------------------------
    app_name: str = "Zoom Breakout Chat Monitor"
    log_level: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"] = "INFO"
    dev_mode: bool = False

    # ----- database ------------------------------------------------------
    # SQLite by default; any SQLAlchemy URL works (e.g. a PostgreSQL DSN).
    database_url: str = "sqlite:///data/zoomchat.db"

    # ----- Zoom ----------------------------------------------------------
    # The only value strictly required to receive chat webhooks.
    zoom_webhook_secret_token: str = ""
    # Server-to-Server OAuth credentials. Optional: only used by the
    # (best-effort) breakout-room name lookup service.
    zoom_client_id: str = ""
    zoom_client_secret: str = ""
    zoom_account_id: str = ""
    zoom_api_base_url: str = "https://api.zoom.us/v2"
    zoom_oauth_token_url: str = "https://zoom.us/oauth/token"
    # Reject webhook deliveries whose x-zm-request-timestamp is older than
    # this many seconds. 0 disables the replay window check.
    zoom_webhook_max_age_seconds: int = 300

    # ----- authentication ------------------------------------------------
    admin_username: str = "admin"
    admin_password: str = ""
    session_secret: str = ""
    session_ttl_hours: int = 12
    cookie_name: str = "zbm_session"
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    # ----- privacy -------------------------------------------------------
    display_private_messages: bool = False
    chat_retention_days: int = 30  # 0 == keep indefinitely
    # Keep the original Zoom delivery alongside the normalised row for
    # debugging. Never served through the API. Private messages are exempt:
    # their raw payload is dropped regardless of this flag.
    store_raw_webhook_payload: bool = True
    retention_sweep_interval_minutes: int = 60

    # ----- behaviour -----------------------------------------------------
    inactivity_warning_minutes: int = 5
    max_request_bytes: int = 1_048_576  # 1 MiB
    messages_page_size: int = 200
    room_recent_messages: int = 50

    # ----- CORS ----------------------------------------------------------
    # Empty by default: the production deployment serves the UI and the API
    # from a single origin through nginx, so no cross-origin access is needed.
    #
    # NoDecode turns off pydantic-settings' automatic JSON decoding of complex
    # types. Without it an env value that is not JSON -- including the empty
    # string that a commented-out .env entry produces -- raises before the
    # validator below ever runs, and the app refuses to start.
    cors_allow_origins: Annotated[List[str], NoDecode] = Field(default_factory=list)

    # ----- rate limiting -------------------------------------------------
    rate_limit_enabled: bool = True
    login_rate_limit_per_minute: int = 10
    webhook_rate_limit_per_minute: int = 600

    _session_secret_is_ephemeral: bool = False

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _parse_origins(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @property
    def session_secret_is_ephemeral(self) -> bool:
        return self._session_secret_is_ephemeral

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def zoom_oauth_configured(self) -> bool:
        return bool(self.zoom_client_id and self.zoom_client_secret and self.zoom_account_id)

    def model_post_init(self, __context: object) -> None:
        if not self.session_secret:
            # Keeps the app bootable for local development, but sessions do
            # not survive a restart. main.py logs a prominent warning.
            object.__setattr__(self, "session_secret", secrets.token_urlsafe(48))
            object.__setattr__(self, "_session_secret_is_ephemeral", True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
