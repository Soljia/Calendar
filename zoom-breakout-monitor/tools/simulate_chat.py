#!/usr/bin/env python3
"""Send simulated Zoom chat webhooks at a running backend.

Unlike the in-process simulator behind ``/api/dev/simulation``, this script
talks to the server over HTTP and signs each delivery exactly the way Zoom
does. That makes it the closest thing to a real meeting you can run without
one: it exercises the signature check, the raw-body handling and the parser.

    python tools/simulate_chat.py --rooms 4 --messages 30

It reads ZOOM_WEBHOOK_SECRET_TOKEN from the environment or a .env file, or
takes --secret. Requires DEV_MODE on the server only insofar as you probably
do not want to point it at production; the endpoint itself is the real one.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    "I agree.",
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

PRIVATE_MESSAGES = [
    "Psst, what did you get for #2?",
    "Do not tell the others, I am lost.",
]


def load_dotenv(path: Path) -> Dict[str, str]:
    """Minimal .env reader; no dependency on python-dotenv."""
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def sign(secret: str, timestamp: str, body: bytes) -> str:
    message = b"v0:" + timestamp.encode() + b":" + body
    return "v0=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def post(url: str, body: bytes, headers: Dict[str, str], timeout: float = 15.0):
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()
    except urllib.error.URLError as exc:
        return None, str(exc.reason)


def build_envelope(
    *,
    meeting_uuid: str,
    meeting_id: str,
    topic: str,
    breakout_room_uuid: Optional[str],
    sender_name: str,
    sender_id: str,
    text: str,
    recipient_type: str = "everyone",
    recipient_name: Optional[str] = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    chat_message: Dict[str, Any] = {
        "date_time": now.isoformat().replace("+00:00", "Z"),
        "message_id": uuid.uuid4().hex,
        "sender_name": sender_name,
        "sender_session_id": sender_id,
        "sender_type": "participant",
        "sender_context": "breakout_room" if breakout_room_uuid else "main_meeting",
        "recipient_context": "breakout_room" if breakout_room_uuid else "main_meeting",
        "recipient_type": recipient_type,
        "message_content": text,
    }
    if recipient_name:
        chat_message["recipient_name"] = recipient_name

    obj: Dict[str, Any] = {
        "id": meeting_id,
        "uuid": meeting_uuid,
        "topic": topic,
        "type": 2,
        "start_time": now.isoformat().replace("+00:00", "Z"),
        "chat_message": chat_message,
    }
    if breakout_room_uuid:
        obj["breakout_room_uuid"] = breakout_room_uuid

    return {
        "event": "meeting.chat_message_sent",
        "event_ts": int(now.timestamp() * 1000),
        "payload": {"account_id": "SIMULATED", "object": obj},
    }


def send(url: str, secret: str, envelope: Dict[str, Any], verbose: bool) -> bool:
    body = json.dumps(envelope).encode()
    timestamp = str(int(time.time() * 1000))
    headers = {
        "Content-Type": "application/json",
        "x-zm-request-timestamp": timestamp,
        "x-zm-signature": sign(secret, timestamp, body),
    }
    status, text = post(url, body, headers)
    chat = envelope["payload"]["object"]["chat_message"]

    if status == 200:
        if verbose:
            print(f"  200  {chat['sender_name']:<9} {chat['message_content']}")
        return True

    print(f"  !! {status}  {text}", file=sys.stderr)
    return False


def validate_endpoint(url: str, secret: str) -> bool:
    """Run Zoom's endpoint.url_validation handshake against the server."""
    plain = base64.urlsafe_b64encode(uuid.uuid4().bytes).decode().rstrip("=")
    envelope = {
        "event": "endpoint.url_validation",
        "event_ts": int(time.time() * 1000),
        "payload": {"plainToken": plain},
    }
    body = json.dumps(envelope).encode()
    timestamp = str(int(time.time() * 1000))
    status, text = post(
        url,
        body,
        {
            "Content-Type": "application/json",
            "x-zm-request-timestamp": timestamp,
            "x-zm-signature": sign(secret, timestamp, body),
        },
    )
    if status != 200:
        print(f"URL validation failed: {status} {text}", file=sys.stderr)
        return False

    expected = hmac.new(secret.encode(), plain.encode(), hashlib.sha256).hexdigest()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        print(f"URL validation returned non-JSON: {text}", file=sys.stderr)
        return False

    if payload.get("plainToken") != plain or payload.get("encryptedToken") != expected:
        print(f"URL validation response did not match: {payload}", file=sys.stderr)
        return False

    print("URL validation handshake OK")
    return True


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate simulated Zoom breakout-room chat traffic.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("WEBHOOK_URL", "http://localhost:8000/api/zoom/webhook"),
        help="Webhook endpoint to post to",
    )
    parser.add_argument("--secret", default=None, help="Zoom webhook secret token")
    parser.add_argument("--env-file", default=".env", help="File to read the secret from")
    parser.add_argument("--topic", default="Chemistry Class", help="Meeting topic")
    parser.add_argument("--rooms", type=int, default=4, help="Number of breakout rooms")
    parser.add_argument(
        "--participants", type=int, default=3, help="Participants per breakout room"
    )
    parser.add_argument(
        "--messages", type=int, default=25, help="Messages to send after seeding (-1 = forever)"
    )
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between messages")
    parser.add_argument(
        "--alert-chance", type=float, default=0.25, help="Probability a message triggers an alert"
    )
    parser.add_argument(
        "--private-chance",
        type=float,
        default=0.0,
        help="Probability a message is a direct message (to exercise privacy filtering)",
    )
    parser.add_argument(
        "--main-meeting-chance",
        type=float,
        default=0.1,
        help="Probability a message comes from the main meeting rather than a breakout room",
    )
    parser.add_argument("--meeting-uuid", default=None, help="Reuse an existing meeting UUID")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only run Zoom's endpoint.url_validation handshake, then exit",
    )
    parser.add_argument("--quiet", action="store_true", help="Only print errors and the summary")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    secret = args.secret or os.environ.get("ZOOM_WEBHOOK_SECRET_TOKEN")
    if not secret:
        here = Path(__file__).resolve().parent.parent
        for candidate in (Path(args.env_file), here / args.env_file):
            secret = load_dotenv(candidate).get("ZOOM_WEBHOOK_SECRET_TOKEN")
            if secret:
                break
    if not secret:
        print(
            "No webhook secret found. Pass --secret, set ZOOM_WEBHOOK_SECRET_TOKEN, "
            "or point --env-file at your .env.",
            file=sys.stderr,
        )
        return 2

    print(f"Target: {args.url}")
    if not validate_endpoint(args.url, secret):
        return 1
    if args.validate_only:
        return 0

    meeting_uuid = args.meeting_uuid or base64.b64encode(uuid.uuid4().bytes).decode()
    meeting_id = str(random.randint(10_000_000_000, 99_999_999_999))

    rooms = []
    for _ in range(args.rooms):
        names = random.sample(FIRST_NAMES, k=min(args.participants, len(FIRST_NAMES)))
        rooms.append(
            {
                "uuid": str(uuid.uuid4()),
                "people": [{"name": n, "id": uuid.uuid4().hex[:16]} for n in names],
            }
        )

    print(f"Meeting: {args.topic}  ({meeting_uuid})")
    print(f"Rooms:   {args.rooms}    Press Ctrl-C to stop.\n")

    sent = 0
    failed = 0

    def emit(room: Optional[Dict[str, Any]], text: Optional[str] = None) -> None:
        nonlocal sent, failed
        if room is None:
            sender = {"name": "Instructor", "id": "host-session"}
            room_uuid = None
        else:
            sender = random.choice(room["people"])
            room_uuid = room["uuid"]

        recipient_type = "everyone"
        recipient_name = None
        if text is None:
            if random.random() < args.private_chance and room is not None:
                text = random.choice(PRIVATE_MESSAGES)
                recipient_type = "individual"
                others = [p for p in room["people"] if p["id"] != sender["id"]]
                recipient_name = random.choice(others)["name"] if others else "Instructor"
            elif random.random() < args.alert_chance:
                text = random.choice(ALERT_MESSAGES)
            else:
                text = random.choice(NEUTRAL_MESSAGES)

        envelope = build_envelope(
            meeting_uuid=meeting_uuid,
            meeting_id=meeting_id,
            topic=args.topic,
            breakout_room_uuid=room_uuid,
            sender_name=sender["name"],
            sender_id=sender["id"],
            text=text,
            recipient_type=recipient_type,
            recipient_name=recipient_name,
        )
        if send(args.url, secret, envelope, verbose=not args.quiet):
            sent += 1
        else:
            failed += 1

    try:
        # Seed each room so the dashboard grid fills immediately.
        for index, room in enumerate(rooms, start=1):
            emit(room, f"Room {index} is ready.")

        remaining = args.messages
        while remaining != 0:
            time.sleep(args.interval)
            if random.random() < args.main_meeting_chance:
                emit(None)
            else:
                emit(random.choice(rooms))
            if remaining > 0:
                remaining -= 1
    except KeyboardInterrupt:
        print("\nInterrupted.")

    print(f"\nSent {sent} message(s), {failed} failure(s).")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
