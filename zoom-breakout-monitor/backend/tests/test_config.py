"""Configuration parsing, including values that come from a .env file."""

from __future__ import annotations

import pytest


def _settings(**env: str):
    import os

    from app.config import Settings

    previous = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    try:
        return Settings()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.mark.parametrize(
    "raw, expected",
    [
        # A commented-out or blank .env entry produces an empty string. This
        # used to crash at startup before the field opted out of JSON decoding.
        ("", []),
        ("   ", []),
        ("https://a.example", ["https://a.example"]),
        ("https://a.example,https://b.example", ["https://a.example", "https://b.example"]),
        (" https://a.example , https://b.example ", ["https://a.example", "https://b.example"]),
        ('["https://a.example"]', ["https://a.example"]),
    ],
)
def test_cors_origins_accept_csv_json_and_empty(raw, expected):
    assert _settings(CORS_ALLOW_ORIGINS=raw).cors_allow_origins == expected


def test_log_level_is_case_insensitive():
    assert _settings(LOG_LEVEL="debug").log_level == "DEBUG"


def test_a_missing_session_secret_is_generated_and_flagged_ephemeral():
    settings = _settings(SESSION_SECRET="")
    assert settings.session_secret
    assert settings.session_secret_is_ephemeral is True


def test_a_configured_session_secret_is_not_ephemeral():
    settings = _settings(SESSION_SECRET="a-real-secret")
    assert settings.session_secret == "a-real-secret"
    assert settings.session_secret_is_ephemeral is False


def test_zoom_oauth_is_only_considered_configured_when_complete():
    assert _settings(ZOOM_CLIENT_ID="a", ZOOM_CLIENT_SECRET="b", ZOOM_ACCOUNT_ID="").zoom_oauth_configured is False
    assert _settings(ZOOM_CLIENT_ID="a", ZOOM_CLIENT_SECRET="b", ZOOM_ACCOUNT_ID="c").zoom_oauth_configured is True


def test_boolean_flags_parse_from_env_strings():
    assert _settings(DEV_MODE="true").dev_mode is True
    assert _settings(DEV_MODE="false").dev_mode is False
    assert _settings(DISPLAY_PRIVATE_MESSAGES="1").display_private_messages is True
