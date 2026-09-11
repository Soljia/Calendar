"""Shared test fixtures.

Environment defaults are set *before* the application package is imported, so
that module-level objects built from configuration (rate limiters, the engine)
are constructed against the test configuration.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"
WEBHOOK_SECRET = "test-webhook-secret-token"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "correct horse battery staple"

os.environ.update(
    {
        "ZOOM_WEBHOOK_SECRET_TOKEN": WEBHOOK_SECRET,
        "ADMIN_USERNAME": ADMIN_USERNAME,
        "ADMIN_PASSWORD": ADMIN_PASSWORD,
        "SESSION_SECRET": "test-session-secret-value",
        "LOG_LEVEL": "CRITICAL",
        "DEV_MODE": "true",
        "DISPLAY_PRIVATE_MESSAGES": "false",
        "CHAT_RETENTION_DAYS": "30",
        "RATE_LIMIT_ENABLED": "false",
        "LOGIN_RATE_LIMIT_PER_MINUTE": "5",
        "WEBHOOK_RATE_LIMIT_PER_MINUTE": "5",
        "DATABASE_URL": "sqlite:///:memory:",
        "COOKIE_SECURE": "false",
    }
)


def load_fixture(name: str) -> Dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text())


def sign_body(body: bytes, timestamp: str, secret: str = WEBHOOK_SECRET) -> str:
    message = b"v0:" + timestamp.encode() + b":" + body
    digest = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return f"v0={digest}"


def webhook_headers(body: bytes, secret: str = WEBHOOK_SECRET) -> Dict[str, str]:
    timestamp = str(int(time.time() * 1000))
    return {
        "x-zm-request-timestamp": timestamp,
        "x-zm-signature": sign_body(body, timestamp, secret),
        "content-type": "application/json",
    }


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fresh database, fresh settings cache and fresh singletons per test."""
    from app import database
    from app.api import deps
    from app.config import get_settings
    from app.services import chat as chat_service
    from app.services.simulator import simulator
    from app.services.websocket import manager

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    get_settings.cache_clear()
    database.reset_engine_for_tests(f"sqlite:///{tmp_path / 'test.db'}")

    manager.reset_for_tests()
    simulator.reset_for_tests()
    deps.login_limiter.reset()
    deps.webhook_limiter.reset()
    chat_service._KEYWORD_CACHE.clear()

    yield

    get_settings.cache_clear()


@pytest.fixture
def configure(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Override environment-backed settings inside a test."""
    from app.config import get_settings

    def _configure(**overrides: Any) -> None:
        for key, value in overrides.items():
            monkeypatch.setenv(key.upper(), str(value))
        get_settings.cache_clear()

    return _configure


@pytest.fixture
def app(isolated_environment):
    from app.main import create_app

    return create_app()


@pytest.fixture
def client(app) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_client(client) -> Any:
    """A TestClient with a valid admin session cookie."""
    response = client.post(
        "/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def db_session(isolated_environment) -> Iterator[Any]:
    """A session against an initialised database (no HTTP app involved)."""
    from app.database import SessionLocal, init_db
    from app.services.settings_store import ensure_settings

    init_db()
    session = SessionLocal()
    ensure_settings(session)
    try:
        yield session
    finally:
        session.close()
