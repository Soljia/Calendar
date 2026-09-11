"""Authentication, session cookies and REST access control."""

from __future__ import annotations

import pytest

from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME

PROTECTED_ENDPOINTS = [
    ("get", "/api/meetings"),
    ("get", "/api/meetings/current"),
    ("get", "/api/settings"),
    ("get", "/api/dev/simulation"),
]


@pytest.mark.parametrize("method, path", PROTECTED_ENDPOINTS)
def test_protected_endpoints_require_authentication(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_settings_write_requires_authentication(client):
    assert client.put("/api/settings", json={}).status_code == 401


def test_room_update_requires_authentication(client):
    assert client.patch("/api/rooms/anything", json={"custom_name": "X"}).status_code == 401


def test_meeting_deletion_requires_authentication(client):
    assert client.delete("/api/meetings/anything?confirm=true").status_code == 401


def test_login_sets_an_httponly_cookie(client):
    response = client.post(
        "/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )

    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert response.json()["username"] == ADMIN_USERNAME

    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "path=/" in set_cookie


@pytest.mark.parametrize(
    "username, password",
    [
        (ADMIN_USERNAME, "wrong password"),
        ("nobody", ADMIN_PASSWORD),
        (ADMIN_USERNAME, ""),
    ],
)
def test_bad_credentials_are_rejected(client, username, password):
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code in (401, 422)
    assert "set-cookie" not in response.headers


def test_authenticated_requests_succeed(auth_client):
    assert auth_client.get("/api/meetings").status_code == 200
    assert auth_client.get("/api/settings").status_code == 200
    assert auth_client.get("/api/auth/me").json()["username"] == ADMIN_USERNAME


def test_logout_clears_the_session(auth_client):
    assert auth_client.post("/api/auth/logout").status_code == 200
    assert auth_client.get("/api/meetings").status_code == 401


def test_session_probe_is_public_and_reports_state(client):
    assert client.get("/api/auth/session").json() == {
        "authenticated": False,
        "username": None,
        "expires_at": None,
    }

    client.post(
        "/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    body = client.get("/api/auth/session").json()
    assert body["authenticated"] is True
    assert body["username"] == ADMIN_USERNAME


def test_a_forged_cookie_is_rejected(client):
    client.cookies.set("zbm_session", "not.a.jwt")
    assert client.get("/api/meetings").status_code == 401


def test_a_token_signed_with_the_wrong_secret_is_rejected(client):
    from app.security import create_session_token

    forged = create_session_token(
        user_id="whoever",
        username="admin",
        token_version=1,
        secret="an-attackers-secret",
        ttl_hours=1,
    )
    client.cookies.set("zbm_session", forged)
    assert client.get("/api/meetings").status_code == 401


def test_an_expired_token_is_rejected(client):
    from app.config import get_settings
    from app.security import create_session_token
    from app.models import AdminUser
    from app.database import SessionLocal

    session = SessionLocal()
    user = session.query(AdminUser).one()
    expired = create_session_token(
        user_id=user.id,
        username=user.username,
        token_version=user.token_version,
        secret=get_settings().session_secret,
        ttl_hours=-1,
    )
    session.close()

    client.cookies.set("zbm_session", expired)
    assert client.get("/api/meetings").status_code == 401


def test_rotating_the_password_invalidates_existing_sessions(auth_client):
    from app.database import SessionLocal
    from app.models import AdminUser

    assert auth_client.get("/api/meetings").status_code == 200

    session = SessionLocal()
    user = session.query(AdminUser).one()
    user.token_version += 1
    session.commit()
    session.close()

    assert auth_client.get("/api/meetings").status_code == 401


def test_password_hashes_are_bcrypt_and_never_plaintext():
    from app.security import hash_password, verify_password

    hashed = hash_password(ADMIN_PASSWORD)
    assert hashed.startswith("$2b$")
    assert ADMIN_PASSWORD not in hashed
    assert verify_password(ADMIN_PASSWORD, hashed) is True
    assert verify_password("something else", hashed) is False


def test_overlong_passwords_are_rejected_rather_than_truncated():
    from app.security import PasswordTooLongError, hash_password

    with pytest.raises(PasswordTooLongError):
        hash_password("x" * 100)


def test_login_is_rate_limited(client, configure):
    from app.api import deps

    configure(RATE_LIMIT_ENABLED="true")
    deps.login_limiter.reset()

    statuses = [
        client.post(
            "/api/auth/login", json={"username": ADMIN_USERNAME, "password": "wrong"}
        ).status_code
        for _ in range(8)
    ]

    assert 429 in statuses
    assert statuses.count(401) == 5  # LOGIN_RATE_LIMIT_PER_MINUTE in conftest


def test_the_webhook_is_not_behind_the_session_cookie(client):
    """Zoom cannot log in; the signature is what authenticates it."""
    import json

    from tests.conftest import load_fixture, webhook_headers

    body = json.dumps(load_fixture("chat_message_breakout.json")).encode()
    assert (
        client.post("/api/zoom/webhook", content=body, headers=webhook_headers(body)).status_code
        == 200
    )
