"""Zoom webhook verification and payload normalisation.

Verification follows Zoom's documented scheme, which is also what Zoom's own
reference receiver (github.com/zoom/webhook-sample) implements:

    message   = "v0:" + <x-zm-request-timestamp> + ":" + <raw request body>
    signature = "v0=" + HMAC_SHA256(secret_token, message).hexdigest()

and is compared against the ``x-zm-signature`` header.

One deliberate difference from Zoom's Node sample: that sample rebuilds the
body with ``JSON.stringify(req.body)``, i.e. it re-serialises a parsed object.
We hash the *raw bytes* exactly as received. Re-serialising is fragile --
any difference in key order, unicode escaping or whitespace breaks the hash --
so the raw body is the only correct input.

PAYLOAD FIELD NAMES: Zoom's event schema is versioned and has changed shape
before, and this build environment could not reach developers.zoom.us to pin
the exact nesting of every field. Rather than guessing one layout, the parser
below looks for each field at every documented/reported location and takes the
first non-empty hit. Unknown fields are never invented -- anything not found
stays ``None``, and the complete original delivery is retained in
``raw_payload`` so a real capture can be inspected. See README -> "Zoom payload
compatibility".
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Sequence

logger = logging.getLogger(__name__)

SIGNATURE_PREFIX = "v0"
URL_VALIDATION_EVENT = "endpoint.url_validation"
CHAT_EVENTS = frozenset({"meeting.chat_message_sent", "webinar.chat_message_sent"})

# Sentinel used when Zoom says a message came from a breakout room but does not
# tell us which one. Grouped separately so the gap is visible in the UI rather
# than silently folded into the main-meeting stream.
UNKNOWN_BREAKOUT_SENTINEL = "__unknown_breakout__"

# recipient_type values that mean "everybody in this room can read it".
# Anything else is treated as a private/direct message. Defaulting to private
# is the safe direction: a misclassified public message is merely hidden,
# whereas a misclassified private one would be exposed.
PUBLIC_RECIPIENT_TYPES = frozenset(
    {
        "everyone",
        "all",
        "everyone_in_meeting",
        "everyone_in_breakout_room",
        "all_participants",
        "everyone_and_attendees",
        "all_panelists_and_attendees",
        "0",
    }
)

BREAKOUT_CONTEXT_VALUES = frozenset({"breakout_room", "breakout", "breakoutroom"})


class WebhookVerificationError(Exception):
    """Raised when a delivery cannot be attributed to Zoom."""


class PayloadParseError(Exception):
    """Raised when a chat event does not contain a usable message."""


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def compute_signature(secret_token: str, timestamp: str, raw_body: bytes) -> str:
    message = b"%s:%s:%s" % (
        SIGNATURE_PREFIX.encode("utf-8"),
        timestamp.encode("utf-8"),
        raw_body,
    )
    digest = hmac.new(secret_token.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}={digest}"


def verify_signature(
    *,
    secret_token: str,
    timestamp: str | None,
    signature: str | None,
    raw_body: bytes,
    max_age_seconds: int = 300,
    now: float | None = None,
) -> None:
    """Validate a delivery. Raises :class:`WebhookVerificationError` on failure."""
    if not secret_token:
        raise WebhookVerificationError("ZOOM_WEBHOOK_SECRET_TOKEN is not configured")
    if not timestamp:
        raise WebhookVerificationError("missing x-zm-request-timestamp header")
    if not signature:
        raise WebhookVerificationError("missing x-zm-signature header")

    if max_age_seconds > 0:
        try:
            # Zoom sends the timestamp in milliseconds since the epoch.
            sent_at = int(timestamp) / 1000.0
        except (TypeError, ValueError) as exc:
            raise WebhookVerificationError("malformed x-zm-request-timestamp header") from exc
        current = time.time() if now is None else now
        if abs(current - sent_at) > max_age_seconds:
            raise WebhookVerificationError("request timestamp outside the accepted replay window")

    expected = compute_signature(secret_token, timestamp, raw_body)
    if not hmac.compare_digest(expected, signature):
        raise WebhookVerificationError("signature mismatch")


def build_url_validation_response(secret_token: str, plain_token: str) -> Dict[str, str]:
    """Answer Zoom's ``endpoint.url_validation`` challenge."""
    if not plain_token:
        raise PayloadParseError("url_validation payload is missing plainToken")
    encrypted = hmac.new(
        secret_token.encode("utf-8"), plain_token.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {"plainToken": plain_token, "encryptedToken": encrypted}


# ---------------------------------------------------------------------------
# Payload normalisation
# ---------------------------------------------------------------------------


@dataclass
class NormalizedChatMessage:
    """Internal representation the rest of the app depends on.

    Nothing downstream of this class ever touches Zoom's raw JSON shape.
    """

    meeting_uuid: str
    zoom_meeting_id: str | None
    breakout_room_uuid: str | None
    sender_name: str
    sender_id: str | None
    message: str
    timestamp: datetime
    sender_context: str | None = None
    recipient_context: str | None = None
    recipient_type: str | None = None
    recipient_name: str | None = None
    recipient_id: str | None = None
    is_private: bool = False
    zoom_message_id: str | None = None
    meeting_topic: str | None = None
    host_id: str | None = None
    meeting_start_time: datetime | None = None
    raw_payload: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_breakout(self) -> bool:
        return bool(self.breakout_room_uuid)


def _first(sources: Sequence[Mapping[str, Any]], keys: Iterable[str]) -> Any:
    """First non-empty value for any of ``keys`` across ``sources``, in order."""
    for key in keys:
        for source in sources:
            if not isinstance(source, Mapping):
                continue
            value = source.get(key)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
    return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_timestamp(value: Any) -> datetime | None:
    """Accept ISO-8601 (with or without ``Z``) or epoch seconds/milliseconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        # Heuristic: Zoom uses epoch milliseconds for event_ts.
        if seconds > 1e11:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return _parse_timestamp(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _normalize_token(value: Any) -> str | None:
    text = _as_str(value)
    return text.lower().replace("-", "_").replace(" ", "_") if text else None


def _decide_private(
    recipient_type: str | None,
    recipient_name: str | None,
    recipient_id: str | None,
) -> bool:
    normalized = _normalize_token(recipient_type)
    if normalized is not None:
        return normalized not in PUBLIC_RECIPIENT_TYPES
    # No recipient_type at all: a named/identified recipient implies a DM.
    return bool(recipient_name or recipient_id)


def is_chat_event(envelope: Mapping[str, Any]) -> bool:
    return str(envelope.get("event", "")) in CHAT_EVENTS


def parse_chat_event(envelope: Mapping[str, Any]) -> NormalizedChatMessage:
    """Normalise a ``*.chat_message_sent`` delivery.

    Raises :class:`PayloadParseError` when the delivery lacks the minimum we
    need (a meeting UUID and a message body).
    """
    if not isinstance(envelope, Mapping):
        raise PayloadParseError("webhook body must be a JSON object")

    payload = envelope.get("payload")
    if not isinstance(payload, Mapping):
        raise PayloadParseError("webhook body is missing 'payload'")

    obj = payload.get("object")
    if not isinstance(obj, Mapping):
        raise PayloadParseError("webhook payload is missing 'object'")

    chat = obj.get("chat_message")
    if not isinstance(chat, Mapping):
        # Some deliveries have been reported with the message fields inlined on
        # the object itself; fall back to that rather than dropping the event.
        chat = {}

    # Search order matters: the chat_message block is more specific than the
    # meeting object, so it wins when both carry the same key.
    scopes: Sequence[Mapping[str, Any]] = (chat, obj, payload)

    meeting_uuid = _as_str(_first((obj, payload), ("uuid", "meeting_uuid")))
    if not meeting_uuid:
        raise PayloadParseError("could not determine the meeting UUID")

    message_text = _first(scopes, ("message_content", "message", "content", "text"))
    if message_text is None:
        raise PayloadParseError("could not determine the message content")

    breakout_uuid = _as_str(
        _first(scopes, ("breakout_room_uuid", "breakout_room_id", "breakout_uuid"))
    )

    sender_context = _as_str(_first(scopes, ("sender_context", "source_context", "context")))
    recipient_context = _as_str(_first(scopes, ("recipient_context", "target_context")))

    # Zoom may signal "this came from a breakout room" without naming which
    # one. Keep those messages, but group them somewhere visibly distinct.
    if not breakout_uuid and _normalize_token(sender_context) in BREAKOUT_CONTEXT_VALUES:
        logger.warning(
            "breakout_room_uuid_missing",
            extra={"meeting_uuid": meeting_uuid, "sender_context": sender_context},
        )
        breakout_uuid = UNKNOWN_BREAKOUT_SENTINEL

    recipient_type = _as_str(_first(scopes, ("recipient_type", "receiver_type", "to_type")))
    recipient_name = _as_str(_first(scopes, ("recipient_name", "receiver_name", "to_name")))
    recipient_id = _as_str(
        _first(scopes, ("recipient_session_id", "recipient_id", "receiver_id", "to_id"))
    )

    timestamp = (
        _parse_timestamp(_first(scopes, ("date_time", "timestamp", "sent_time", "send_time")))
        or _parse_timestamp(envelope.get("event_ts"))
        or datetime.now(timezone.utc)
    )

    return NormalizedChatMessage(
        meeting_uuid=meeting_uuid,
        zoom_meeting_id=_as_str(_first((obj, payload), ("id", "meeting_id"))),
        breakout_room_uuid=breakout_uuid,
        sender_name=_as_str(
            _first(scopes, ("sender_name", "sender", "display_name", "user_name"))
        )
        or "Unknown",
        sender_id=_as_str(
            _first(
                scopes,
                (
                    "sender_session_id",
                    "sender_id",
                    "sender_participant_id",
                    "participant_user_id",
                    "sender_user_id",
                ),
            )
        ),
        message=str(message_text),
        timestamp=timestamp,
        sender_context=sender_context,
        recipient_context=recipient_context,
        recipient_type=recipient_type,
        recipient_name=recipient_name,
        recipient_id=recipient_id,
        is_private=_decide_private(recipient_type, recipient_name, recipient_id),
        # Deliberately not searching for a bare "id" here: on the meeting
        # object that key is the meeting ID, not a message ID.
        zoom_message_id=_as_str(_first(scopes, ("message_id", "msg_id"))),
        meeting_topic=_as_str(_first((obj, payload), ("topic",))),
        host_id=_as_str(_first((obj, payload), ("host_id",))),
        meeting_start_time=_parse_timestamp(_first((obj, payload), ("start_time",))),
        raw_payload=dict(envelope),
    )
