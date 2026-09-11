"""Normalisation of Zoom chat payloads into the internal ChatMessage shape."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests.conftest import load_fixture


def test_parses_a_breakout_room_message():
    from app.services.zoom import parse_chat_event

    message = parse_chat_event(load_fixture("chat_message_breakout.json"))

    assert message.meeting_uuid == "aBcDeFgHiJkLmNoPqRsTuA=="
    assert message.zoom_meeting_id == "81234567890"
    assert message.breakout_room_uuid == "11111111-1111-4111-8111-111111111111"
    assert message.sender_name == "Amy"
    assert message.sender_id == "sender-session-amy"
    assert message.message == "Can the teacher come help?"
    assert message.sender_context == "breakout_room"
    assert message.recipient_context == "breakout_room"
    assert message.recipient_type == "everyone"
    assert message.is_private is False
    assert message.is_breakout is True
    assert message.zoom_message_id == "msg-breakout-0001"
    assert message.meeting_topic == "Chemistry Class"
    assert message.timestamp == datetime(2026, 9, 11, 16, 10, 3, tzinfo=timezone.utc)


def test_main_meeting_messages_have_no_breakout_uuid():
    from app.services.zoom import parse_chat_event

    message = parse_chat_event(load_fixture("chat_message_main_meeting.json"))

    assert message.breakout_room_uuid is None
    assert message.is_breakout is False
    assert message.sender_context == "main_meeting"


def test_private_message_is_flagged():
    from app.services.zoom import parse_chat_event

    message = parse_chat_event(load_fixture("chat_message_private.json"))

    assert message.is_private is True
    assert message.recipient_type == "individual"
    assert message.recipient_name == "Bob"


def test_alternate_field_layout_is_accepted():
    """breakout_room_uuid nested inside chat_message, and 'message' for the body."""
    from app.services.zoom import parse_chat_event

    message = parse_chat_event(load_fixture("chat_message_alternate_layout.json"))

    assert message.breakout_room_uuid == "33333333-3333-4333-8333-333333333333"
    assert message.message == "We are confused about step two."
    assert message.sender_id == "sender-carol"


def test_breakout_context_without_a_uuid_is_kept_and_marked():
    from app.services.zoom import UNKNOWN_BREAKOUT_SENTINEL, parse_chat_event

    message = parse_chat_event(load_fixture("chat_message_missing_breakout_uuid.json"))

    assert message.breakout_room_uuid == UNKNOWN_BREAKOUT_SENTINEL
    assert message.is_breakout is True


def test_raw_payload_is_preserved():
    from app.services.zoom import parse_chat_event

    original = load_fixture("chat_message_breakout.json")
    message = parse_chat_event(original)

    assert message.raw_payload == original


@pytest.mark.parametrize(
    "envelope, expected",
    [
        ({}, "missing 'payload'"),
        ({"payload": {}}, "missing 'object'"),
        ({"payload": {"object": {"chat_message": {}}}}, "meeting UUID"),
        (
            {"payload": {"object": {"uuid": "u", "chat_message": {"sender_name": "x"}}}},
            "message content",
        ),
    ],
)
def test_unusable_payloads_raise(envelope, expected):
    from app.services.zoom import PayloadParseError, parse_chat_event

    with pytest.raises(PayloadParseError, match=expected):
        parse_chat_event(envelope)


@pytest.mark.parametrize(
    "recipient_type, expected_private",
    [
        ("everyone", False),
        ("Everyone", False),
        ("all", False),
        ("everyone_in_breakout_room", False),
        ("individual", True),
        ("host", True),
        ("direct", True),
    ],
)
def test_recipient_type_decides_privacy(recipient_type, expected_private):
    from app.services.zoom import parse_chat_event

    envelope = {
        "event": "meeting.chat_message_sent",
        "payload": {
            "object": {
                "uuid": "meeting-uuid",
                "chat_message": {
                    "sender_name": "A",
                    "message_content": "hi",
                    "recipient_type": recipient_type,
                },
            }
        },
    }
    assert parse_chat_event(envelope).is_private is expected_private


def test_an_identified_recipient_without_a_type_is_treated_as_private():
    """Defaulting to private is the safe direction when Zoom omits the type."""
    from app.services.zoom import parse_chat_event

    envelope = {
        "event": "meeting.chat_message_sent",
        "payload": {
            "object": {
                "uuid": "meeting-uuid",
                "chat_message": {
                    "sender_name": "A",
                    "message_content": "hi",
                    "recipient_name": "Bob",
                },
            }
        },
    }
    assert parse_chat_event(envelope).is_private is True


def test_epoch_millisecond_event_ts_is_used_when_no_date_time_is_present():
    from app.services.zoom import parse_chat_event

    envelope = {
        "event": "meeting.chat_message_sent",
        "event_ts": 1757000000000,
        "payload": {
            "object": {
                "uuid": "meeting-uuid",
                "chat_message": {"sender_name": "A", "message_content": "hi"},
            }
        },
    }
    parsed = parse_chat_event(envelope)
    assert parsed.timestamp == datetime.fromtimestamp(1757000000, tz=timezone.utc)


def test_meeting_id_is_not_mistaken_for_a_message_id():
    from app.services.zoom import parse_chat_event

    envelope = {
        "event": "meeting.chat_message_sent",
        "payload": {
            "object": {
                "id": "81234567890",
                "uuid": "meeting-uuid",
                "chat_message": {"sender_name": "A", "message_content": "hi"},
            }
        },
    }
    assert parse_chat_event(envelope).zoom_message_id is None


def test_is_chat_event_recognises_meeting_and_webinar_variants():
    from app.services.zoom import is_chat_event

    assert is_chat_event({"event": "meeting.chat_message_sent"}) is True
    assert is_chat_event({"event": "webinar.chat_message_sent"}) is True
    assert is_chat_event({"event": "meeting.participant_joined"}) is False
