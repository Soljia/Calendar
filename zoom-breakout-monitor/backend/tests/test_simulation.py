"""Simulation mode, including that it is fully absent when DEV_MODE is false."""

from __future__ import annotations

import pytest


@pytest.fixture
def prod_app(configure, isolated_environment):
    """An application built with DEV_MODE=false."""
    from app.main import create_app

    configure(DEV_MODE="false")
    return create_app()


@pytest.fixture
def prod_client(prod_app):
    from fastapi.testclient import TestClient

    with TestClient(prod_app) as client:
        yield client


@pytest.fixture
def prod_auth_client(prod_client):
    from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME

    response = prod_client.post(
        "/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200
    return prod_client


# --- DEV_MODE=false: the feature does not exist -------------------------


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/api/dev/simulation"),
        ("post", "/api/dev/simulation/start"),
        ("post", "/api/dev/simulation/stop"),
    ],
)
def test_simulation_routes_are_absent_in_production(prod_auth_client, method, path):
    call = getattr(prod_auth_client, method)
    response = call(path) if method == "get" else call(path, json={})
    assert response.status_code == 404


def test_simulation_routes_are_not_in_the_production_openapi_document(prod_client):
    paths = prod_client.get("/api/openapi.json").json()["paths"]
    assert not any(path.startswith("/api/dev") for path in paths)


async def test_the_simulator_refuses_to_run_with_dev_mode_off(configure):
    from app.schemas import SimulationRequest
    from app.services.simulator import SimulationDisabledError, simulator

    configure(DEV_MODE="false")

    with pytest.raises(SimulationDisabledError):
        await simulator.start(SimulationRequest())


def test_health_reports_dev_mode_off(prod_client):
    assert prod_client.get("/api/health").json()["dev_mode"] is False


# --- DEV_MODE=true: the feature works -----------------------------------


def test_simulation_endpoints_require_authentication(client):
    assert client.get("/api/dev/simulation").status_code == 401


def test_simulation_status_starts_idle(auth_client):
    assert auth_client.get("/api/dev/simulation").json()["running"] is False


def test_a_simulation_populates_rooms_and_pushes_over_the_socket(auth_client):
    with auth_client.websocket_connect("/ws") as socket:
        socket.receive_json()  # hello

        response = auth_client.post(
            "/api/dev/simulation/start",
            json={
                "topic": "Simulated Chemistry",
                "rooms": 4,
                "participants_per_room": 3,
                "messages": 0,  # the seed message per room is enough
                "interval_seconds": 0.1,
            },
        )
        assert response.status_code == 200
        assert response.json()["running"] is True

        # Frames arrive interleaved with simulation_status updates, so read
        # until every room has reported in rather than counting blind.
        seen = []
        while sum(1 for e in seen if e["type"] == "chat_message") < 4:
            seen.append(socket.receive_json())
            assert len(seen) < 30, "simulator did not seed every room"

    types = [event["type"] for event in seen]
    assert types.count("room_created") == 4
    assert types.count("chat_message") == 4

    chats = [event for event in seen if event["type"] == "chat_message"]
    assert {c["data"]["room"]["room_number"] for c in chats} == {1, 2, 3, 4}

    meetings = auth_client.get("/api/meetings").json()
    assert meetings[0]["topic"] == "Simulated Chemistry"
    assert meetings[0]["simulated"] is True
    assert meetings[0]["room_count"] == 4


def test_simulated_messages_are_marked_as_simulated(auth_client):
    auth_client.post(
        "/api/dev/simulation/start",
        json={"rooms": 2, "messages": 0, "interval_seconds": 0.1},
    )
    meeting = auth_client.get("/api/meetings").json()[0]
    page = auth_client.get(f"/api/meetings/{meeting['id']}/messages").json()

    assert page["total"] == 2
    assert all(item["simulated"] is True for item in page["items"])


def test_a_running_simulation_cannot_be_started_twice(auth_client):
    payload = {"rooms": 2, "messages": 50, "interval_seconds": 5.0}
    assert auth_client.post("/api/dev/simulation/start", json=payload).status_code == 200
    assert auth_client.post("/api/dev/simulation/start", json=payload).status_code == 409
    auth_client.post("/api/dev/simulation/stop")


def test_a_simulation_can_be_stopped(auth_client):
    auth_client.post(
        "/api/dev/simulation/start",
        json={"rooms": 2, "messages": 100, "interval_seconds": 5.0},
    )
    body = auth_client.post("/api/dev/simulation/stop").json()

    assert body["running"] is False
    assert auth_client.get("/api/dev/simulation").json()["running"] is False


def test_simulation_request_validation(auth_client):
    assert (
        auth_client.post("/api/dev/simulation/start", json={"rooms": 0}).status_code == 422
    )
    assert (
        auth_client.post("/api/dev/simulation/start", json={"rooms": 500}).status_code == 422
    )


def test_simulated_envelopes_pass_the_real_parser():
    """The simulator must exercise the production code path, not a shortcut."""
    from app.services.simulator import build_chat_envelope
    from app.services.zoom import is_chat_event, parse_chat_event

    envelope = build_chat_envelope(
        meeting_uuid="sim-uuid",
        meeting_id="81234567890",
        topic="Sim",
        breakout_room_uuid="room-1",
        sender_name="Alice",
        sender_id="p1",
        text="Can someone help us?",
        sender_context="breakout_room",
    )

    assert is_chat_event(envelope) is True
    parsed = parse_chat_event(envelope)
    assert parsed.breakout_room_uuid == "room-1"
    assert parsed.sender_name == "Alice"
    assert parsed.message == "Can someone help us?"
    assert parsed.is_private is False
