"""WebSocket fan-out.

Clients subscribe either to a single meeting (``/ws/meetings/{uuid}``) or to
the dashboard-wide feed (``/ws``). Broadcasts are best-effort: a socket that
errors is dropped rather than allowed to stall ingestion.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)

GLOBAL_CHANNEL = "*"


class ConnectionManager:
    def __init__(self) -> None:
        self._channels: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, channel: str = GLOBAL_CHANNEL) -> None:
        await websocket.accept()
        async with self._lock:
            self._channels.setdefault(channel, set()).add(websocket)
        logger.info(
            "websocket_client_connected",
            extra={"channel": channel, "clients": self.client_count(channel)},
        )

    async def disconnect(self, websocket: WebSocket, channel: str = GLOBAL_CHANNEL) -> None:
        async with self._lock:
            listeners = self._channels.get(channel)
            if listeners:
                listeners.discard(websocket)
                if not listeners:
                    self._channels.pop(channel, None)
        logger.info(
            "websocket_client_disconnected",
            extra={"channel": channel, "clients": self.client_count(channel)},
        )

    def client_count(self, channel: str | None = None) -> int:
        if channel is None:
            return sum(len(v) for v in self._channels.values())
        return len(self._channels.get(channel, ()))

    async def send_personal(self, websocket: WebSocket, event: Dict[str, Any]) -> None:
        try:
            await websocket.send_json(event)
        except Exception:  # pragma: no cover - transport failure
            logger.debug("websocket_send_failed")

    async def broadcast(self, event: Dict[str, Any], channel: str = GLOBAL_CHANNEL) -> None:
        """Send to ``channel`` and, unless it *is* the global channel, to it too.

        Meeting-scoped clients get their meeting's traffic; the dashboard feed
        sees everything, which is what drives the home page live counters.
        """
        targets: Set[WebSocket] = set()
        async with self._lock:
            targets |= set(self._channels.get(channel, ()))
            if channel != GLOBAL_CHANNEL:
                targets |= set(self._channels.get(GLOBAL_CHANNEL, ()))

        if not targets:
            return

        results = await asyncio.gather(
            *(socket.send_json(event) for socket in targets),
            return_exceptions=True,
        )
        dead = [sock for sock, res in zip(targets, results) if isinstance(res, Exception)]
        if dead:
            async with self._lock:
                for listeners in self._channels.values():
                    for socket in dead:
                        listeners.discard(socket)
            logger.debug("websocket_dropped_dead_clients", extra={"count": len(dead)})

    def reset_for_tests(self) -> None:
        """Drop all state and rebind the lock.

        The lock binds to the first event loop that awaits it, so a long-lived
        module singleton has to be reset between tests that each run their own
        loop.
        """
        self._channels.clear()
        self._lock = asyncio.Lock()

    async def close_all(self) -> None:
        async with self._lock:
            sockets = [s for listeners in self._channels.values() for s in listeners]
            self._channels.clear()
        await asyncio.gather(*(s.close() for s in sockets), return_exceptions=True)


manager = ConnectionManager()
