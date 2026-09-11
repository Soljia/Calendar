"""REST surface: meetings, rooms, messages, filtering, settings, deletion."""

from __future__ import annotations

import json

import pytest

from tests.conftest import load_fixture, webhook_headers


def post_webhook(client, fixture_name: str, **overrides):
    envelope = load_fixture(fixture_name)
    chat = envelope["payload"]["object"]["chat_message"]
    for key, value in overrides.items():
        chat[key] = value
    body = json.dumps(envelope).encode()
    response = client.post("/api/zoom/webhook", content=body, headers=webhook_headers(body))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def populated(auth_client):
    post_webhook(auth_client, "chat_message_breakout.json")
    post_webhook(auth_client, "chat_message_breakout_second_room.json")
    post_webhook(auth_client, "chat_message_main_meeting.json")
    meeting = auth_client.get("/api/meetings").json()[0]
    return auth_client, meeting


def test_list_meetings(populated):
    client, meeting = populated

    body = client.get("/api/meetings").json()

    assert len(body) == 1
    assert body[0]["topic"] == "Chemistry Class"
    assert body[0]["zoom_meeting_id"] == "81234567890"
    assert body[0]["meeting_uuid"] == "aBcDeFgHiJkLmNoPqRsTuA=="
    assert body[0]["room_count"] == 3  # main meeting + two breakout rooms
    assert body[0]["message_count"] == 3
    assert body[0]["alert_count"] == 1
    assert body[0]["active"] is True


def test_current_meeting(populated):
    client, meeting = populated
    assert client.get("/api/meetings/current").json()["id"] == meeting["id"]


def test_current_meeting_is_null_when_there_are_none(auth_client):
    assert auth_client.get("/api/meetings/current").json() is None


def test_meeting_detail_includes_rooms_and_recent_messages(populated):
    client, meeting = populated

    detail = client.get(f"/api/meetings/{meeting['id']}").json()

    assert detail["topic"] == "Chemistry Class"
    rooms = detail["rooms"]
    assert [r["room_number"] for r in rooms] == [0, 1, 2]
    assert [r["name"] for r in rooms] == [
        "Main Meeting",
        "Breakout Room 1",
        "Breakout Room 2",
    ]
    room_one = rooms[1]
    assert room_one["alert_count"] == 1
    assert room_one["recent_messages"][0]["message"] == "Can the teacher come help?"
    assert room_one["recent_messages"][0]["is_alert"] is True
    assert sorted(room_one["recent_messages"][0]["matched_keywords"]) == ["help", "teacher"]


def test_meeting_can_also_be_addressed_by_its_zoom_uuid(populated):
    client, meeting = populated
    from urllib.parse import quote

    response = client.get(f"/api/meetings/{quote(meeting['meeting_uuid'], safe='')}")
    assert response.status_code == 200
    assert response.json()["id"] == meeting["id"]


def test_unknown_meeting_returns_404(auth_client):
    assert auth_client.get("/api/meetings/does-not-exist").status_code == 404


def test_list_rooms(populated):
    client, meeting = populated

    rooms = client.get(f"/api/meetings/{meeting['id']}/rooms").json()

    assert len(rooms) == 3
    assert rooms[1]["breakout_room_uuid"] == "11111111-1111-4111-8111-111111111111"
    assert rooms[0]["is_main_room"] is True


def test_combined_message_feed_is_chronological(populated):
    client, meeting = populated

    page = client.get(f"/api/meetings/{meeting['id']}/messages").json()

    assert page["total"] == 3
    timestamps = [item["timestamp"] for item in page["items"]]
    assert timestamps == sorted(timestamps)
    assert [item["sender_name"] for item in page["items"]] == ["Amy", "John", "Instructor"]
    # Every entry carries the room it belongs to, for the combined view.
    assert [item["room_name"] for item in page["items"]] == [
        "Breakout Room 1",
        "Breakout Room 2",
        "Main Meeting",
    ]


def test_timestamps_are_serialised_as_utc(populated):
    client, meeting = populated
    page = client.get(f"/api/meetings/{meeting['id']}/messages").json()
    assert page["items"][0]["timestamp"].endswith("Z") or "+00:00" in page["items"][0]["timestamp"]


def test_messages_can_be_filtered_by_room(populated):
    client, meeting = populated
    rooms = client.get(f"/api/meetings/{meeting['id']}/rooms").json()
    room_one = next(r for r in rooms if r["room_number"] == 1)

    page = client.get(
        f"/api/meetings/{meeting['id']}/messages", params={"room_id": room_one["id"]}
    ).json()

    assert page["total"] == 1
    assert page["items"][0]["sender_name"] == "Amy"


def test_messages_can_be_filtered_by_sender(populated):
    client, meeting = populated

    page = client.get(
        f"/api/meetings/{meeting['id']}/messages", params={"sender": "joh"}
    ).json()

    assert page["total"] == 1
    assert page["items"][0]["sender_name"] == "John"


def test_messages_can_be_searched_by_content(populated):
    client, meeting = populated

    page = client.get(f"/api/meetings/{meeting['id']}/messages", params={"q": "42"}).json()

    assert page["total"] == 1
    assert "42" in page["items"][0]["message"]


def test_search_does_not_allow_sql_injection(populated):
    client, meeting = populated

    page = client.get(
        f"/api/meetings/{meeting['id']}/messages",
        params={"q": "'; DROP TABLE chat_messages; --"},
    ).json()

    assert page["total"] == 0
    # The table is still there.
    assert client.get(f"/api/meetings/{meeting['id']}/messages").json()["total"] == 3


def test_messages_can_be_filtered_to_alerts_only(populated):
    client, meeting = populated

    page = client.get(
        f"/api/meetings/{meeting['id']}/messages", params={"alerts_only": True}
    ).json()

    assert page["total"] == 1
    assert page["items"][0]["is_alert"] is True


def test_messages_can_be_filtered_by_time(populated):
    client, meeting = populated

    page = client.get(
        f"/api/meetings/{meeting['id']}/messages",
        params={"since": "2026-09-11T16:10:10Z"},
    ).json()

    assert page["total"] == 2
    assert [i["sender_name"] for i in page["items"]] == ["John", "Instructor"]


def test_message_pagination(populated):
    client, meeting = populated

    page = client.get(
        f"/api/meetings/{meeting['id']}/messages", params={"limit": 2, "offset": 0}
    ).json()

    assert page["total"] == 3
    assert len(page["items"]) == 2
    assert page["limit"] == 2


# --- rooms --------------------------------------------------------------


def test_a_room_can_be_renamed(populated):
    client, meeting = populated
    rooms = client.get(f"/api/meetings/{meeting['id']}/rooms").json()
    room = next(r for r in rooms if r["room_number"] == 1)

    response = client.patch(f"/api/rooms/{room['id']}", json={"custom_name": "Titration Team"})

    assert response.status_code == 200
    assert response.json()["name"] == "Titration Team"
    assert response.json()["custom_name"] == "Titration Team"
    # The rename survives a refetch and shows on messages too.
    feed = client.get(f"/api/meetings/{meeting['id']}/messages").json()
    assert feed["items"][0]["room_name"] == "Titration Team"


def test_clearing_a_custom_name_restores_the_generated_one(populated):
    client, meeting = populated
    rooms = client.get(f"/api/meetings/{meeting['id']}/rooms").json()
    room = next(r for r in rooms if r["room_number"] == 2)

    client.patch(f"/api/rooms/{room['id']}", json={"custom_name": "Temporary"})
    response = client.patch(f"/api/rooms/{room['id']}", json={"custom_name": None})

    assert response.json()["name"] == "Breakout Room 2"


def test_renaming_an_unknown_room_returns_404(auth_client):
    assert (
        auth_client.patch("/api/rooms/nope", json={"custom_name": "X"}).status_code == 404
    )


def test_room_name_suggestions_degrade_gracefully_without_oauth(populated):
    client, meeting = populated

    body = client.get(f"/api/meetings/{meeting['id']}/room-name-suggestions").json()

    assert body["available"] is False
    assert "OAuth" in body["reason"]
    assert body["names"] == []


# --- settings -----------------------------------------------------------


def test_settings_defaults(auth_client):
    body = auth_client.get("/api/settings").json()

    assert body["alerts_enabled"] is True
    assert sorted(body["alert_keywords"]) == sorted(
        ["help", "teacher", "question", "stuck", "confused", "problem"]
    )
    assert body["browser_notifications_enabled"] is False
    assert body["inactivity_warning_minutes"] == 5


def test_settings_round_trip(auth_client):
    response = auth_client.put(
        "/api/settings",
        json={
            "alerts_enabled": True,
            "alert_keywords": ["  HELP  ", "help", "urgent", ""],
            "browser_notifications_enabled": True,
            "inactivity_warning_minutes": 9,
        },
    )

    assert response.status_code == 200
    body = response.json()
    # Normalised: trimmed, lowercased, de-duplicated, blanks dropped.
    assert body["alert_keywords"] == ["help", "urgent"]
    assert body["inactivity_warning_minutes"] == 9
    assert auth_client.get("/api/settings").json()["alert_keywords"] == ["help", "urgent"]


def test_settings_reject_an_out_of_range_threshold(auth_client):
    response = auth_client.put(
        "/api/settings",
        json={
            "alerts_enabled": True,
            "alert_keywords": [],
            "browser_notifications_enabled": False,
            "inactivity_warning_minutes": 0,
        },
    )
    assert response.status_code == 422


# --- deletion -----------------------------------------------------------


def test_deleting_a_meeting_removes_its_rooms_and_messages(populated):
    client, meeting = populated

    response = client.delete(f"/api/meetings/{meeting['id']}", params={"confirm": True})

    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert body["rooms_deleted"] == 3
    assert body["messages_deleted"] == 3
    assert client.get("/api/meetings").json() == []
    assert client.get(f"/api/meetings/{meeting['id']}").status_code == 404


def test_deletion_requires_explicit_confirmation(populated):
    client, meeting = populated

    assert client.delete(f"/api/meetings/{meeting['id']}").status_code == 400
    assert client.get("/api/meetings").json() != []


def test_openapi_document_is_generated(client):
    body = client.get("/api/openapi.json").json()

    assert body["info"]["title"] == "Zoom Breakout Chat Monitor"
    assert "/api/zoom/webhook" in body["paths"]
    assert "/api/meetings/{meeting_uuid}/messages" in body["paths"]


def test_health_endpoint(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_security_headers_are_set(client):
    headers = client.get("/api/health").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
