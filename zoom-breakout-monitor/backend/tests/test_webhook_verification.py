"""Zoom webhook signature verification and the URL-validation handshake."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from tests.conftest import WEBHOOK_SECRET, load_fixture, sign_body, webhook_headers


def test_signature_matches_zoom_reference_algorithm():
    from app.services.zoom import compute_signature

    body = b'{"event":"test"}'
    timestamp = "1757000000000"
    expected = hmac.new(
        WEBHOOK_SECRET.encode(),
        b"v0:" + timestamp.encode() + b":" + body,
        hashlib.sha256,
    ).hexdigest()

    assert compute_signature(WEBHOOK_SECRET, timestamp, body) == f"v0={expected}"


def test_verify_signature_accepts_a_valid_delivery():
    from app.services.zoom import verify_signature

    body = json.dumps(load_fixture("chat_message_breakout.json")).encode()
    timestamp = str(int(time.time() * 1000))
    verify_signature(
        secret_token=WEBHOOK_SECRET,
        timestamp=timestamp,
        signature=sign_body(body, timestamp),
        raw_body=body,
    )


@pytest.mark.parametrize(
    "signature",
    ["v0=deadbeef", "totally-wrong", "v0=", ""],
)
def test_verify_signature_rejects_bad_signatures(signature):
    from app.services.zoom import WebhookVerificationError, verify_signature

    body = b'{"event":"test"}'
    timestamp = str(int(time.time() * 1000))
    with pytest.raises(WebhookVerificationError):
        verify_signature(
            secret_token=WEBHOOK_SECRET,
            timestamp=timestamp,
            signature=signature or None,
            raw_body=body,
        )


def test_verify_signature_rejects_a_body_that_was_tampered_with():
    from app.services.zoom import WebhookVerificationError, verify_signature

    original = b'{"event":"a"}'
    timestamp = str(int(time.time() * 1000))
    signature = sign_body(original, timestamp)

    with pytest.raises(WebhookVerificationError):
        verify_signature(
            secret_token=WEBHOOK_SECRET,
            timestamp=timestamp,
            signature=signature,
            raw_body=b'{"event":"b"}',
        )


def test_verify_signature_rejects_a_replayed_delivery():
    from app.services.zoom import WebhookVerificationError, verify_signature

    body = b'{"event":"test"}'
    old_timestamp = str(int((time.time() - 3600) * 1000))
    with pytest.raises(WebhookVerificationError, match="replay window"):
        verify_signature(
            secret_token=WEBHOOK_SECRET,
            timestamp=old_timestamp,
            signature=sign_body(body, old_timestamp),
            raw_body=body,
            max_age_seconds=300,
        )


def test_verify_signature_requires_a_configured_secret():
    from app.services.zoom import WebhookVerificationError, verify_signature

    with pytest.raises(WebhookVerificationError, match="not configured"):
        verify_signature(
            secret_token="", timestamp="1", signature="v0=x", raw_body=b"{}"
        )


def test_webhook_endpoint_rejects_an_invalid_signature(client):
    body = json.dumps(load_fixture("chat_message_breakout.json")).encode()
    response = client.post(
        "/api/zoom/webhook",
        content=body,
        headers={
            "x-zm-request-timestamp": str(int(time.time() * 1000)),
            "x-zm-signature": "v0=not-the-right-hash",
            "content-type": "application/json",
        },
    )
    assert response.status_code == 401


def test_webhook_endpoint_rejects_missing_headers(client):
    body = json.dumps(load_fixture("chat_message_breakout.json")).encode()
    response = client.post("/api/zoom/webhook", content=body)
    assert response.status_code == 401


def test_url_validation_handshake_returns_the_expected_token_pair(client):
    payload = load_fixture("url_validation.json")
    body = json.dumps(payload).encode()

    response = client.post("/api/zoom/webhook", content=body, headers=webhook_headers(body))

    assert response.status_code == 200
    data = response.json()
    plain = payload["payload"]["plainToken"]
    expected = hmac.new(WEBHOOK_SECRET.encode(), plain.encode(), hashlib.sha256).hexdigest()
    assert data == {"plainToken": plain, "encryptedToken": expected}


def test_url_validation_still_requires_a_valid_signature(client):
    body = json.dumps(load_fixture("url_validation.json")).encode()
    response = client.post(
        "/api/zoom/webhook",
        content=body,
        headers={
            "x-zm-request-timestamp": str(int(time.time() * 1000)),
            "x-zm-signature": "v0=wrong",
        },
    )
    assert response.status_code == 401


def test_webhook_rejects_a_body_that_is_not_json(client):
    body = b"not json at all"
    response = client.post("/api/zoom/webhook", content=body, headers=webhook_headers(body))
    assert response.status_code == 400


def test_webhook_rejects_an_oversized_body(client):
    body = b'{"padding":"' + b"x" * 2_000_000 + b'"}'
    response = client.post("/api/zoom/webhook", content=body, headers=webhook_headers(body))
    assert response.status_code == 413


def test_unsubscribed_events_are_acknowledged_but_not_stored(client):
    body = json.dumps(load_fixture("participant_joined.json")).encode()
    response = client.post("/api/zoom/webhook", content=body, headers=webhook_headers(body))
    assert response.status_code == 200
    assert response.json() == {"received": True, "handled": False}
