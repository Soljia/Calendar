"""Meeting/room creation, breakout grouping, persistence and alert matching."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import load_fixture


def _ingest(db, fixture_name: str, **kwargs):
    from app.services.chat import ingest_chat_message
    from app.services.zoom import parse_chat_event

    outcome = ingest_chat_message(db, parse_chat_event(load_fixture(fixture_name)), **kwargs)
    db.commit()
    return outcome


# --- meetings & rooms ---------------------------------------------------


def test_first_message_creates_the_meeting_and_its_room(db_session):
    from app.models import BreakoutRoom, Meeting

    outcome = _ingest(db_session, "chat_message_breakout.json")

    assert outcome.stored is True
    meeting = db_session.query(Meeting).one()
    assert meeting.zoom_meeting_uuid == "aBcDeFgHiJkLmNoPqRsTuA=="
    assert meeting.zoom_meeting_id == "81234567890"
    assert meeting.topic == "Chemistry Class"
    assert meeting.active is True

    room = db_session.query(BreakoutRoom).one()
    assert room.breakout_room_uuid == "11111111-1111-4111-8111-111111111111"
    assert room.room_number == 1
    assert room.display_name == "Breakout Room 1"
    assert room.is_main_room is False


def test_a_second_message_reuses_the_same_meeting(db_session):
    from app.models import Meeting

    _ingest(db_session, "chat_message_breakout.json")
    _ingest(db_session, "chat_message_breakout_second_room.json")

    assert db_session.query(Meeting).count() == 1


def test_messages_are_grouped_by_breakout_room_uuid(db_session):
    from app.models import BreakoutRoom, ChatMessage

    _ingest(db_session, "chat_message_breakout.json")
    _ingest(db_session, "chat_message_breakout_second_room.json")

    rooms = db_session.query(BreakoutRoom).order_by(BreakoutRoom.room_number).all()
    assert [r.room_number for r in rooms] == [1, 2]
    assert [r.display_name for r in rooms] == ["Breakout Room 1", "Breakout Room 2"]

    by_room = {r.id: r for r in rooms}
    for message in db_session.query(ChatMessage).all():
        assert by_room[message.room_id].breakout_room_uuid == message.breakout_room_uuid


def test_repeated_messages_from_one_room_do_not_create_extra_rooms(db_session):
    from app.models import BreakoutRoom
    from app.services.chat import ingest_chat_message
    from app.services.zoom import parse_chat_event

    for index in range(5):
        envelope = load_fixture("chat_message_breakout.json")
        envelope["payload"]["object"]["chat_message"]["message_id"] = f"msg-{index}"
        ingest_chat_message(db_session, parse_chat_event(envelope))
    db_session.commit()

    assert db_session.query(BreakoutRoom).count() == 1
    assert db_session.query(BreakoutRoom).one().room_number == 1


def test_room_numbering_is_stable_across_interleaved_traffic(db_session):
    from app.models import BreakoutRoom
    from app.services.chat import ingest_chat_message
    from app.services.zoom import parse_chat_event

    order = ["c", "a", "b", "a", "c", "a"]
    for index, room_key in enumerate(order):
        envelope = load_fixture("chat_message_breakout.json")
        envelope["payload"]["object"]["breakout_room_uuid"] = f"uuid-{room_key}"
        envelope["payload"]["object"]["chat_message"]["message_id"] = f"m{index}"
        ingest_chat_message(db_session, parse_chat_event(envelope))
    db_session.commit()

    numbers = {
        r.breakout_room_uuid: r.room_number for r in db_session.query(BreakoutRoom).all()
    }
    # Numbered by first appearance, and never renumbered afterwards.
    assert numbers == {"uuid-c": 1, "uuid-a": 2, "uuid-b": 3}


def test_main_meeting_messages_land_in_a_reserved_room_zero(db_session):
    from app.models import BreakoutRoom

    _ingest(db_session, "chat_message_main_meeting.json")

    room = db_session.query(BreakoutRoom).one()
    assert room.is_main_room is True
    assert room.room_number == 0
    assert room.display_name == "Main Meeting"


def test_main_meeting_and_breakout_rooms_coexist(db_session):
    from app.models import BreakoutRoom

    _ingest(db_session, "chat_message_main_meeting.json")
    _ingest(db_session, "chat_message_breakout.json")

    rooms = db_session.query(BreakoutRoom).order_by(BreakoutRoom.room_number).all()
    assert [r.room_number for r in rooms] == [0, 1]
    # The main meeting must not consume a breakout room number.
    assert rooms[1].display_name == "Breakout Room 1"


def test_a_message_with_an_unidentified_breakout_room_is_labelled(db_session):
    from app.models import BreakoutRoom

    _ingest(db_session, "chat_message_missing_breakout_uuid.json")

    room = db_session.query(BreakoutRoom).one()
    assert room.display_name == "Breakout Room (unidentified)"
    assert room.is_main_room is False


# --- persistence --------------------------------------------------------


def test_message_fields_are_persisted(db_session):
    from app.models import ChatMessage

    _ingest(db_session, "chat_message_breakout.json")

    message = db_session.query(ChatMessage).one()
    assert message.sender_name == "Amy"
    assert message.sender_id == "sender-session-amy"
    assert message.message == "Can the teacher come help?"
    assert message.zoom_message_id == "msg-breakout-0001"
    assert message.recipient_type == "everyone"
    assert message.is_private is False
    assert message.simulated is False
    assert message.raw_payload is not None
    assert message.raw_payload["event"] == "meeting.chat_message_sent"


def test_duplicate_deliveries_are_ignored(db_session):
    from app.models import ChatMessage

    first = _ingest(db_session, "chat_message_breakout.json")
    second = _ingest(db_session, "chat_message_breakout.json")

    assert first.stored is True
    assert second.stored is False
    assert second.duplicate is True
    assert db_session.query(ChatMessage).count() == 1


def test_room_and_meeting_activity_timestamps_advance(db_session):
    from app.models import BreakoutRoom, Meeting

    _ingest(db_session, "chat_message_breakout.json")
    room = db_session.query(BreakoutRoom).one()
    meeting = db_session.query(Meeting).one()
    first_seen = room.first_seen

    _ingest(db_session, "chat_message_breakout_second_room.json")
    db_session.refresh(meeting)
    db_session.refresh(room)

    assert room.first_seen == first_seen
    assert meeting.last_activity >= room.last_activity - timedelta(seconds=1)


# --- keyword alerts -----------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Can the teacher come help?", ["help", "teacher"]),
        ("HELP US PLEASE", ["help"]),
        ("We are Stuck on #5", ["stuck"]),
        ("I'm confused", ["confused"]),
        ("helping each other out", ["help"]),
        ("The answer is 42.", []),
        ("A whelp of a problem", ["problem"]),
    ],
)
def test_keyword_matching_is_case_insensitive_and_word_anchored(text, expected):
    from app.config import DEFAULT_ALERT_KEYWORDS
    from app.services.chat import match_keywords

    assert sorted(match_keywords(text, DEFAULT_ALERT_KEYWORDS)) == sorted(expected)


def test_alerting_message_is_flagged_and_counted(db_session):
    from app.models import BreakoutRoom, ChatMessage

    _ingest(db_session, "chat_message_breakout.json")

    message = db_session.query(ChatMessage).one()
    assert message.is_alert is True
    assert sorted(message.matched_keywords) == ["help", "teacher"]
    assert db_session.query(BreakoutRoom).one().alert_count == 1


def test_non_alerting_message_is_not_flagged(db_session):
    from app.models import ChatMessage

    _ingest(db_session, "chat_message_breakout_second_room.json")

    message = db_session.query(ChatMessage).one()
    assert message.is_alert is False
    assert message.matched_keywords is None


def test_alerts_can_be_disabled(db_session):
    from app.models import ChatMessage
    from app.schemas import DashboardSettings
    from app.services.settings_store import save_settings

    save_settings(
        db_session,
        DashboardSettings(alerts_enabled=False, alert_keywords=["help"]),
    )
    _ingest(db_session, "chat_message_breakout.json")

    assert db_session.query(ChatMessage).one().is_alert is False


def test_custom_keywords_replace_the_defaults(db_session):
    from app.models import ChatMessage
    from app.schemas import DashboardSettings
    from app.services.settings_store import save_settings

    save_settings(db_session, DashboardSettings(alert_keywords=["banana"]))
    _ingest(db_session, "chat_message_breakout.json")
    assert db_session.query(ChatMessage).one().is_alert is False

    save_settings(db_session, DashboardSettings(alert_keywords=["teacher"]))
    _ingest(db_session, "chat_message_breakout_second_room.json")
