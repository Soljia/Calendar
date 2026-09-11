"""WebSocket authentication and message distribution."""

from __future__ import annotations

import json

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.conftest import ADMIN_USERNAME, load_fixture, webhook_headers


def send_chat(client, fixture_name: str = "chat_message_breakout.json", **chat_overrides):
    envelope = load_fixture(fixture_name)
    envelope["payload"]["object"]["chat_message"].update(chat_overrides)
    body = json.dumps(envelope).encode()
    response = client.post("/api/zoom/webhook", content=body, headers=webhook_headers(body))
    assert response.status_code == 200, response.text
    return response.json()


def test_websocket_requires_authentication(client):
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws") as socket:
            socket.receive_json()
    assert excinfo.value.code == 1008


def test_authenticated_client_receives_a_hello_frame(auth_client):
    with auth_client.websocket_connect("/ws") as socket:
        hello = socket.receive_json()

    assert hello["type"] == "hello"
    assert hello["data"]["scope"] == "dashboard"
    assert hello["data"]["user"] == ADMIN_USERNAME


def test_ping_is_answered_with_pong(auth_client):
    with auth_client.websocket_connect("/ws") as socket:
        socket.receive_json()  # hello
        socket.send_text("ping")
        assert socket.receive_json()["type"] == "pong"


def test_a_chat_message_reaches_the_dashboard_feed(auth_client):
    with auth_client.websocket_connect("/ws") as socket:
        socket.receive_json()  # hello
        send_chat(auth_client)

        events = {}
        for _ in range(3):
            event = socket.receive_json()
            events[event["type"]] = event["data"]

    assert set(events) == {"meeting_created", "room_created", "chat_message"}

    chat = events["chat_message"]
    assert chat["room"]["name"] == "Breakout Room 1"
    assert chat["room"]["room_number"] == 1
    assert chat["message"]["sender_name"] == "Amy"
    assert chat["message"]["message"] == "Can the teacher come help?"
    assert chat["message"]["is_alert"] is True
    assert sorted(chat["message"]["matched_keywords"]) == ["help", "teacher"]
    assert "timestamp" in chat["message"]


def test_meeting_scoped_socket_receives_that_meetings_traffic(auth_client):
    send_chat(auth_client)
    meeting = auth_client.get("/api/meetings").json()[0]

    with auth_client.websocket_connect(f"/ws/meetings/{meeting['id']}") as socket:
        hello = socket.receive_json()
        assert hello["data"]["scope"] == "meeting"
        assert hello["data"]["meeting_uuid"] == meeting["meeting_uuid"]

        send_chat(auth_client, "chat_message_breakout_second_room.json")

        types = []
        for _ in range(2):
            types.append(socket.receive_json()["type"])

    assert types == ["room_created", "chat_message"]


def test_meeting_socket_rejects_an_unknown_meeting(auth_client):
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with auth_client.websocket_connect("/ws/meetings/nope") as socket:
            socket.receive_json()
    assert excinfo.value.code == 1008


def test_every_connected_client_receives_the_broadcast(auth_client):
    with auth_client.websocket_connect("/ws") as first:
        with auth_client.websocket_connect("/ws") as second:
            first.receive_json()
            second.receive_json()

            send_chat(auth_client)

            for socket in (first, second):
                types = [socket.receive_json()["type"] for _ in range(3)]
                assert "chat_message" in types


def test_private_messages_are_not_pushed_over_the_socket(auth_client):
    with auth_client.websocket_connect("/ws") as socket:
        socket.receive_json()  # hello

        send_chat(auth_client, "chat_message_private.json")
        # Only the meeting/room creation events should arrive.
        first = socket.receive_json()
        second = socket.receive_json()

        # Then a normal message, proving nothing private was queued ahead of it.
        send_chat(auth_client, "chat_message_breakout_second_room.json")
        third = socket.receive_json()
        fourth = socket.receive_json()

    assert {first["type"], second["type"]} == {"meeting_created", "room_created"}
    assert third["type"] == "room_created"
    assert fourth["type"] == "chat_message"
    assert fourth["data"]["message"]["sender_name"] == "John"


def test_renaming_a_room_is_broadcast(auth_client):
    send_chat(auth_client)
    meeting = auth_client.get("/api/meetings").json()[0]
    room = auth_client.get(f"/api/meetings/{meeting['id']}/rooms").json()[0]

    with auth_client.websocket_connect("/ws") as socket:
        socket.receive_json()  # hello
        auth_client.patch(f"/api/rooms/{room['id']}", json={"custom_name": "Lab Group A"})
        event = socket.receive_json()

    assert event["type"] == "room_updated"
    assert event["data"]["name"] == "Lab Group A"


def test_settings_changes_are_broadcast(auth_client):
    with auth_client.websocket_connect("/ws") as socket:
        socket.receive_json()  # hello
        auth_client.put(
            "/api/settings",
            json={
                "alerts_enabled": True,
                "alert_keywords": ["help"],
                "browser_notifications_enabled": False,
                "inactivity_warning_minutes": 7,
            },
        )
        event = socket.receive_json()

    assert event["type"] == "settings_updated"
    assert event["data"]["inactivity_warning_minutes"] == 7


def test_meeting_deletion_is_broadcast(auth_client):
    send_chat(auth_client)
    meeting = auth_client.get("/api/meetings").json()[0]

    with auth_client.websocket_connect("/ws") as socket:
        socket.receive_json()  # hello
        auth_client.delete(f"/api/meetings/{meeting['id']}", params={"confirm": True})
        event = socket.receive_json()

    assert event["type"] == "meeting_deleted"
    assert event["data"]["meeting_uuid"] == meeting["meeting_uuid"]


def test_a_logged_out_session_cannot_open_a_socket(auth_client):
    auth_client.post("/api/auth/logout")

    with pytest.raises(WebSocketDisconnect):
        with auth_client.websocket_connect("/ws") as socket:
            socket.receive_json()
