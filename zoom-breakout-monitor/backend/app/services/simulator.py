"""Development-only chat simulator.

Generates Zoom-shaped ``meeting.chat_message_sent`` envelopes and pushes them
through the *same* parser, ingestion and broadcast path a real delivery takes,
so the dashboard can be exercised end to end without a live Zoom meeting.

Gated entirely on ``DEV_MODE``: :func:`app.api.dev` is not mounted when it is
false, and :meth:`Simulator.start` refuses to run regardless.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi.concurrency import run_in_threadpool

from ..config import get_settings
from ..database import session_scope
from ..schemas import SimulationRequest, SimulationStatus
from .chat import ingest_chat_message
from .websocket import manager
from .zoom import parse_chat_event

logger = logging.getLogger(__name__)

FIRST_NAMES = [
    "Alice", "Bob", "Carol", "David", "Amy", "John", "Lisa", "Mike",
    "Sarah", "Tom", "Nina", "Omar", "Priya", "Quinn", "Rosa", "Sam",
]

NEUTRAL_MESSAGES = [
    "We're finished.",
    "I think the answer is 12.",
    "We got 42.",
    "Has anyone done #3?",
    "I'm working on it.",
    "Give us another minute.",
    "Let's compare our answers.",
    "Maybe 24?",
    "We got #3.",
    "I agree.",
    "Reading the instructions now.",
    "Our group split the work.",
]

ALERT_MESSAGES = [
    "Can the teacher come help?",
    "We are stuck on #5.",
    "I'm confused about the second step.",
    "Can someone help us?",
    "We have a question about the formula.",
    "There's a problem with our setup.",
]


class SimulationDisabledError(RuntimeError):
    """Raised when simulation is requested while DEV_MODE is off."""


def _fake_meeting_uuid() -> str:
    # Shaped like a Zoom meeting UUID (base64 of 16 random bytes).
    return base64.b64encode(uuid.uuid4().bytes).decode("ascii")


def _fake_meeting_id() -> str:
    return str(random.randint(10_000_000_000, 99_999_999_999))


def build_chat_envelope(
    *,
    meeting_uuid: str,
    meeting_id: str,
    topic: str,
    breakout_room_uuid: Optional[str],
    sender_name: str,
    sender_id: str,
    text: str,
    recipient_type: str = "everyone",
    sender_context: Optional[str] = None,
    when: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build a ``meeting.chat_message_sent`` envelope.

    Field names mirror Zoom's documented meeting webhook shape. The parser
    accepts several alternative layouts (see services/zoom.py), but the
    simulator emits the primary one so the happy path is what gets exercised.
    """
    now = when or datetime.now(timezone.utc)
    chat_message: Dict[str, Any] = {
        "date_time": now.isoformat().replace("+00:00", "Z"),
        "message_id": uuid.uuid4().hex,
        "sender_name": sender_name,
        "sender_session_id": sender_id,
        "sender_type": "participant",
        "recipient_type": recipient_type,
        "message_content": text,
    }
    if sender_context:
        chat_message["sender_context"] = sender_context
        chat_message["recipient_context"] = sender_context

    obj: Dict[str, Any] = {
        "id": meeting_id,
        "uuid": meeting_uuid,
        "topic": topic,
        "type": 2,
        "chat_message": chat_message,
    }
    if breakout_room_uuid:
        obj["breakout_room_uuid"] = breakout_room_uuid

    return {
        "event": "meeting.chat_message_sent",
        "event_ts": int(now.timestamp() * 1000),
        "payload": {"account_id": "SIMULATED", "object": obj},
    }


async def deliver_envelope(envelope: Dict[str, Any], *, simulated: bool = True) -> None:
    """Run one envelope through parse -> persist -> broadcast."""
    normalized = parse_chat_event(envelope)

    def _persist():
        with session_scope() as db:
            return ingest_chat_message(db, normalized, simulated=simulated)

    outcome = await run_in_threadpool(_persist)
    for event in outcome.events:
        await manager.broadcast(event, channel=outcome.meeting_uuid)


class Simulator:
    """Owns at most one running simulation at a time."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._status = SimulationStatus(running=False)
        self._lock = asyncio.Lock()

    @staticmethod
    def enabled() -> bool:
        return get_settings().dev_mode

    def status(self) -> SimulationStatus:
        return self._status.model_copy()

    def reset_for_tests(self) -> None:
        self._task = None
        self._status = SimulationStatus(running=False)
        self._lock = asyncio.Lock()

    async def start(self, request: SimulationRequest) -> SimulationStatus:
        if not self.enabled():
            raise SimulationDisabledError("simulation is unavailable because DEV_MODE is false")

        async with self._lock:
            if self._task and not self._task.done():
                raise RuntimeError("a simulation is already running")

            meeting_uuid = request.meeting_uuid or _fake_meeting_uuid()
            meeting_id = _fake_meeting_id()
            rooms = [
                {"uuid": str(uuid.uuid4()), "participants": _pick_participants(request.participants_per_room)}
                for _ in range(request.rooms)
            ]
            self._status = SimulationStatus(
                running=True,
                meeting_uuid=meeting_uuid,
                topic=request.topic,
                rooms=request.rooms,
                messages_sent=0,
                messages_planned=request.messages,
            )

            # Seed every room before returning, so the caller (and the
            # dashboard) sees a fully populated meeting the moment the request
            # completes, instead of racing the background task.
            for index, room in enumerate(rooms, start=1):
                await self._emit(
                    request, meeting_uuid, meeting_id, room,
                    text=f"Room {index} is ready.",
                )

            self._task = asyncio.create_task(
                self._run(request, meeting_uuid, meeting_id, rooms),
                name="chat-simulator",
            )

        logger.info(
            "simulation_started",
            extra={
                "meeting_uuid": meeting_uuid,
                "rooms": request.rooms,
                "messages": request.messages,
            },
        )
        return self.status()

    async def stop(self) -> SimulationStatus:
        async with self._lock:
            task = self._task
            self._task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - best effort
                pass
        self._status = self._status.model_copy(update={"running": False})
        logger.info("simulation_stopped", extra={"meeting_uuid": self._status.meeting_uuid})
        await manager.broadcast(
            {"type": "simulation_status", "data": self._status.model_dump(mode="json")}
        )
        return self.status()

    async def _run(
        self,
        request: SimulationRequest,
        meeting_uuid: str,
        meeting_id: str,
        rooms: List[Dict[str, Any]],
    ) -> None:
        try:
            remaining = request.messages
            while remaining > 0:
                await asyncio.sleep(request.interval_seconds)
                room = random.choice(rooms)
                await self._emit(request, meeting_uuid, meeting_id, room)
                remaining -= 1
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            logger.exception("simulation_failed")
        finally:
            self._status = self._status.model_copy(update={"running": False})
            await manager.broadcast(
                {"type": "simulation_status", "data": self._status.model_dump(mode="json")}
            )

    async def _emit(
        self,
        request: SimulationRequest,
        meeting_uuid: str,
        meeting_id: str,
        room: Dict[str, Any],
        *,
        text: Optional[str] = None,
    ) -> None:
        sender = random.choice(room["participants"])
        if text is None:
            pool = NEUTRAL_MESSAGES
            if request.include_alerts and random.random() < 0.25:
                pool = ALERT_MESSAGES
            text = random.choice(pool)

        envelope = build_chat_envelope(
            meeting_uuid=meeting_uuid,
            meeting_id=meeting_id,
            topic=request.topic,
            breakout_room_uuid=room["uuid"],
            sender_name=sender["name"],
            sender_id=sender["id"],
            text=text,
            sender_context="breakout_room",
        )
        await deliver_envelope(envelope)
        self._status = self._status.model_copy(
            update={"messages_sent": self._status.messages_sent + 1}
        )


def _pick_participants(count: int) -> List[Dict[str, str]]:
    names = random.sample(FIRST_NAMES, k=min(count, len(FIRST_NAMES)))
    return [{"name": name, "id": uuid.uuid4().hex[:16]} for name in names]


simulator = Simulator()
