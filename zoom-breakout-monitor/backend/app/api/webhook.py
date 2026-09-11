"""Zoom webhook receiver.

Nothing here trusts the request until :func:`verify_signature` has passed, and
the signature is computed over the *raw* body bytes rather than a re-serialised
copy of the parsed JSON.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool

from ..config import Settings
from ..database import session_scope
from ..services.chat import ingest_chat_message
from ..services.websocket import manager
from ..services.zoom import (
    URL_VALIDATION_EVENT,
    PayloadParseError,
    WebhookVerificationError,
    build_url_validation_response,
    is_chat_event,
    parse_chat_event,
    verify_signature,
)
from .deps import enforce_rate_limit, settings_dep, webhook_limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/zoom", tags=["zoom"])


async def _read_body(request: Request, max_bytes: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Request body too large",
                )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed Content-Length"
            ) from None

    body = await request.body()
    if len(body) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Request body too large",
        )
    return body


@router.post("/webhook")
async def zoom_webhook(
    request: Request,
    settings: Settings = Depends(settings_dep),
) -> Response:
    enforce_rate_limit(webhook_limiter, request, name="zoom_webhook")

    raw_body = await _read_body(request, settings.max_request_bytes)
    logger.info("zoom_webhook_received", extra={"bytes": len(raw_body)})

    try:
        verify_signature(
            secret_token=settings.zoom_webhook_secret_token,
            timestamp=request.headers.get("x-zm-request-timestamp"),
            signature=request.headers.get("x-zm-signature"),
            raw_body=raw_body,
            max_age_seconds=settings.zoom_webhook_max_age_seconds,
        )
    except WebhookVerificationError as exc:
        logger.warning("webhook_verification_failed", extra={"reason": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature"
        ) from exc

    try:
        envelope: Dict[str, Any] = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        logger.warning("webhook_body_not_json")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Body is not valid JSON"
        ) from exc

    if not isinstance(envelope, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Body must be a JSON object"
        )

    event_name = str(envelope.get("event", ""))

    # --- Zoom's endpoint ownership challenge ----------------------------
    if event_name == URL_VALIDATION_EVENT:
        payload = envelope.get("payload") or {}
        plain_token = payload.get("plainToken") if isinstance(payload, dict) else None
        try:
            body = build_url_validation_response(
                settings.zoom_webhook_secret_token, str(plain_token or "")
            )
        except PayloadParseError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        logger.info("webhook_url_validation_answered")
        return Response(content=json.dumps(body), media_type="application/json")

    if not is_chat_event(envelope):
        # Subscribing to extra events must not produce errors on Zoom's side;
        # acknowledge and move on.
        logger.debug("webhook_event_ignored", extra={"zoom_event": event_name})
        return Response(
            content=json.dumps({"received": True, "handled": False}),
            media_type="application/json",
        )

    try:
        normalized = parse_chat_event(envelope)
    except PayloadParseError as exc:
        # 200, not 4xx: Zoom retries non-2xx responses, and a payload we cannot
        # read will not become readable on a retry.
        logger.warning(
            "chat_payload_unparseable",
            extra={"zoom_event": event_name, "reason": str(exc)},
        )
        return Response(
            content=json.dumps({"received": True, "handled": False, "reason": str(exc)}),
            media_type="application/json",
        )

    def _persist():
        with session_scope() as db:
            return ingest_chat_message(db, normalized, simulated=False)

    outcome = await run_in_threadpool(_persist)
    for event in outcome.events:
        await manager.broadcast(event, channel=outcome.meeting_uuid)

    return Response(
        content=json.dumps(
            {"received": True, "handled": outcome.stored, "duplicate": outcome.duplicate}
        ),
        media_type="application/json",
    )
