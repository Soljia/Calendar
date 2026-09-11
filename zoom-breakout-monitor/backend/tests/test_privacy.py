"""Private-message handling and retention."""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import load_fixture


def _ingest(db, fixture_name: str):
    from app.services.chat import ingest_chat_message
    from app.services.zoom import parse_chat_event

    outcome = ingest_chat_message(db, parse_chat_event(load_fixture(fixture_name)))
    db.commit()
    return outcome


def test_private_message_body_is_not_stored_by_default(db_session):
    from app.models import ChatMessage

    outcome = _ingest(db_session, "chat_message_private.json")

    assert outcome.stored is True
    assert outcome.visible is False

    message = db_session.query(ChatMessage).one()
    assert message.is_private is True
    # Metadata is retained for correct processing; content never hits disk.
    assert message.message == ""
    assert message.raw_payload is None
    assert message.recipient_name is None
    assert message.matched_keywords is None
    assert message.is_alert is False


def test_private_message_is_not_broadcast(db_session):
    outcome = _ingest(db_session, "chat_message_private.json")

    assert not any(event["type"] == "chat_message" for event in outcome.events)


def test_private_message_does_not_count_towards_room_totals(db_session):
    from app.models import BreakoutRoom
    from app.services.serializers import room_to_out

    _ingest(db_session, "chat_message_private.json")

    room = db_session.query(BreakoutRoom).one()
    assert room.message_count == 0
    assert room_to_out(db_session, room, include_messages=True).message_count == 0


def test_private_message_is_hidden_from_the_rest_api(auth_client):
    import json

    from tests.conftest import webhook_headers

    for fixture in ("chat_message_breakout.json", "chat_message_private.json"):
        body = json.dumps(load_fixture(fixture)).encode()
        assert (
            auth_client.post(
                "/api/zoom/webhook", content=body, headers=webhook_headers(body)
            ).status_code
            == 200
        )

    meetings = auth_client.get("/api/meetings").json()
    messages = auth_client.get(f"/api/meetings/{meetings[0]['id']}/messages").json()

    assert messages["total"] == 1
    assert messages["items"][0]["message"] == "Can the teacher come help?"
    assert all("do not tell the others" not in m["message"] for m in messages["items"])


def test_private_messages_are_stored_and_shown_when_explicitly_enabled(
    db_session, configure
):
    from app.models import ChatMessage
    from app.services.serializers import room_to_out
    from app.models import BreakoutRoom

    configure(DISPLAY_PRIVATE_MESSAGES="true")
    outcome = _ingest(db_session, "chat_message_private.json")

    assert outcome.visible is True
    message = db_session.query(ChatMessage).one()
    assert message.message == "I am stuck, do not tell the others."
    assert message.is_private is True
    assert message.is_alert is True  # "stuck" matched
    assert message.recipient_name == "Bob"

    room = db_session.query(BreakoutRoom).one()
    assert room_to_out(db_session, room).message_count == 1
    assert any(event["type"] == "chat_message" for event in outcome.events)


def test_settings_endpoint_surfaces_the_private_message_policy(auth_client):
    body = auth_client.get("/api/settings").json()
    assert body["display_private_messages"] is False
    assert "chat_retention_days" in body


def test_private_message_display_cannot_be_enabled_through_the_api(auth_client):
    payload = auth_client.get("/api/settings").json()
    payload["display_private_messages"] = True

    response = auth_client.put("/api/settings", json=payload)

    assert response.status_code == 200
    assert response.json()["display_private_messages"] is False


def test_raw_payload_is_never_exposed_through_the_api(auth_client):
    import json

    from tests.conftest import webhook_headers

    body = json.dumps(load_fixture("chat_message_breakout.json")).encode()
    auth_client.post("/api/zoom/webhook", content=body, headers=webhook_headers(body))

    meetings = auth_client.get("/api/meetings").json()
    detail = auth_client.get(f"/api/meetings/{meetings[0]['id']}").text
    messages = auth_client.get(f"/api/meetings/{meetings[0]['id']}/messages").text

    for payload in (detail, messages):
        assert "raw_payload" not in payload
        assert "SANITIZED_ACCOUNT" not in payload


def test_raw_payload_storage_can_be_turned_off(db_session, configure):
    from app.models import ChatMessage

    configure(STORE_RAW_WEBHOOK_PAYLOAD="false")
    _ingest(db_session, "chat_message_breakout.json")

    assert db_session.query(ChatMessage).one().raw_payload is None


# --- retention ----------------------------------------------------------


def test_retention_purges_messages_older_than_the_window(db_session):
    from app.models import BreakoutRoom, ChatMessage, utcnow
    from app.services.retention import purge_expired_messages

    _ingest(db_session, "chat_message_breakout.json")
    _ingest(db_session, "chat_message_breakout_second_room.json")

    old = db_session.query(ChatMessage).first()
    old.received_at = utcnow() - timedelta(days=45)
    db_session.commit()

    removed = purge_expired_messages(db_session, retention_days=30)
    db_session.commit()

    assert removed == 1
    assert db_session.query(ChatMessage).count() == 1
    # Cached per-room counters are re-synced after a purge.
    assert sum(r.message_count for r in db_session.query(BreakoutRoom).all()) == 1


def test_retention_of_zero_keeps_everything(db_session):
    from app.models import ChatMessage, utcnow
    from app.services.retention import purge_expired_messages

    _ingest(db_session, "chat_message_breakout.json")
    db_session.query(ChatMessage).one().received_at = utcnow() - timedelta(days=4000)
    db_session.commit()

    assert purge_expired_messages(db_session, retention_days=0) == 0
    assert db_session.query(ChatMessage).count() == 1


def test_stale_meetings_are_marked_inactive(db_session):
    from app.models import Meeting, utcnow
    from app.services.retention import mark_stale_meetings_inactive

    _ingest(db_session, "chat_message_breakout.json")
    meeting = db_session.query(Meeting).one()
    meeting.last_activity = utcnow() - timedelta(hours=48)
    db_session.commit()

    assert mark_stale_meetings_inactive(db_session, idle_hours=12) == 1
    db_session.commit()
    assert db_session.query(Meeting).one().active is False
