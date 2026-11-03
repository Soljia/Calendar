#!/usr/bin/env python3
"""
Near-instant Gmail forwarder for Venmo "paid you" emails.

Holds an IMAP IDLE connection open to Gmail. Gmail pushes a notification the
moment a new message lands, and the worker then:
  1. fetches the new message(s),
  2. checks that the subject/body contain ALL of REQUIRED_TERMS,
  3. forwards a copy to every address in FORWARD_TO via Gmail SMTP,
  4. adds a Gmail label so the message is never forwarded twice.

On startup (and after any reconnect) it also catches up on matching mail
from the last LOOKBACK_DAYS that is not yet labeled, so nothing is lost
while the worker is down.

Configuration is via environment variables -- see .env.example.
"""

from __future__ import annotations

import email
import email.policy
import html
import logging
import os
import re
import smtplib
import socket
import ssl
import sys
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr

from imapclient import IMAPClient

log = logging.getLogger("venmo-forwarder")

# Header added to every forward; messages carrying it are never re-forwarded.
LOOP_HEADER = "X-Venmo-Forwarder"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    address: str
    app_password: str
    forward_to: list[str]
    required_terms: list[str]
    allowed_senders: tuple[str, ...] = ()  # e.g. ("venmo.com",); empty = anyone
    label: str = "Venmo-Forwarded"
    folder: str = "INBOX"
    lookback_days: int = 2
    idle_seconds: int = 9 * 60  # Gmail drops idle connections after ~10 min
    imap_host: str = "imap.gmail.com"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465

    @classmethod
    def from_env(cls) -> "Config":
        def req(name: str) -> str:
            value = os.environ.get(name, "").strip()
            if not value:
                sys.exit(f"Missing required environment variable {name}")
            return value

        def split(value: str) -> list[str]:
            return [v.strip() for v in value.split(",") if v.strip()]

        forward_to = split(req("FORWARD_TO"))
        bad = [a for a in forward_to if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", a)]
        if bad:
            sys.exit(f"Invalid FORWARD_TO address(es): {', '.join(bad)}")

        return cls(
            address=req("GMAIL_ADDRESS"),
            app_password=req("GMAIL_APP_PASSWORD").replace(" ", ""),
            forward_to=forward_to,
            required_terms=[t.lower() for t in split(os.environ.get("REQUIRED_TERMS", "paid you,venmo"))],
            allowed_senders=tuple(s.lower().lstrip("@") for s in split(os.environ.get("ALLOWED_SENDERS", ""))),
            label=os.environ.get("PROCESSED_LABEL", "Venmo-Forwarded").strip(),
            folder=os.environ.get("IMAP_FOLDER", "INBOX").strip(),
            lookback_days=int(os.environ.get("LOOKBACK_DAYS", "2")),
        )


# ---------------------------------------------------------------------------
# Message helpers (pure functions -- covered by test_forwarder.py)
# ---------------------------------------------------------------------------

def _html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style).*?</\1>", " ", markup)
    text = re.sub(r"(?s)<[^>]+>", " ", markup)
    return re.sub(r"\s+", " ", html.unescape(text))


def _part_content(msg: EmailMessage, subtype: str) -> str | None:
    part = msg.get_body(preferencelist=(subtype,))
    if part is None:
        return None
    try:
        return part.get_content()
    except (LookupError, UnicodeDecodeError):
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", errors="replace")


def searchable_text(msg: EmailMessage) -> str:
    """Subject + plain body + tag-stripped HTML body, lowercased."""
    pieces = [str(msg.get("Subject", ""))]
    plain = _part_content(msg, "plain")
    rich = _part_content(msg, "html")
    if plain:
        pieces.append(plain)
    if rich:
        pieces.append(_html_to_text(rich))
    return re.sub(r"\s+", " ", "\n".join(pieces)).lower()


def should_forward(msg: EmailMessage, cfg: Config) -> bool:
    if msg.get(LOOP_HEADER):
        return False  # one of our own forwards
    sender = parseaddr(str(msg.get("From", "")))[1].lower()
    if sender == cfg.address.lower():
        return False  # mail we sent ourselves
    if cfg.allowed_senders and not any(
        sender == a or sender.endswith("@" + a) or sender.endswith("." + a)
        for a in cfg.allowed_senders
    ):
        return False
    text = searchable_text(msg)
    return all(term in text for term in cfg.required_terms)


def build_forward(original: EmailMessage, cfg: Config) -> EmailMessage:
    subject = str(original.get("Subject", ""))
    header_lines = [
        "---------- Forwarded message ---------",
        f"From: {original.get('From', '')}",
        f"Date: {original.get('Date', '')}",
        f"Subject: {subject}",
        f"To: {original.get('To', '')}",
    ]

    fwd = EmailMessage()
    fwd["Subject"] = subject if subject.lower().startswith("fwd:") else f"Fwd: {subject}"
    fwd["From"] = cfg.address
    fwd["To"] = ", ".join(cfg.forward_to)
    fwd[LOOP_HEADER] = "1"

    plain = _part_content(original, "plain")
    rich = _part_content(original, "html")
    body_text = plain if plain is not None else _html_to_text(rich or "")
    fwd.set_content("\n".join(header_lines) + "\n\n" + body_text)

    if rich:
        header_html = "<br>".join(html.escape(line) for line in header_lines)
        fwd.add_alternative(
            f'<div style="color:#555">{header_html}</div><br>{rich}', subtype="html"
        )
    return fwd


# ---------------------------------------------------------------------------
# IMAP / SMTP
# ---------------------------------------------------------------------------

def _labels(raw) -> set[str]:
    out = set()
    for item in raw or ():
        value = item.decode("utf-8", "replace") if isinstance(item, bytes) else str(item)
        out.add(value.strip('"'))
    return out


def send(fwd: EmailMessage, cfg: Config) -> None:
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=ctx, timeout=60) as smtp:
        smtp.login(cfg.address, cfg.app_password)
        smtp.send_message(fwd)


def process_uids(client: IMAPClient, cfg: Config, uids) -> None:
    uids = sorted(uids)
    if not uids:
        return
    # BODY.PEEK[] fetches without marking the message as read.
    fetched = client.fetch(uids, ["BODY.PEEK[]", "X-GM-LABELS"])
    for uid in uids:
        data = fetched.get(uid)
        if not data:
            continue
        if cfg.label in _labels(data.get(b"X-GM-LABELS")):
            continue
        msg = email.message_from_bytes(data[b"BODY[]"], policy=email.policy.default)
        if not should_forward(msg, cfg):
            continue
        # Send first, label second: if sending fails the exception bubbles up,
        # we reconnect, and the catch-up search retries this message.
        send(build_forward(msg, cfg), cfg)
        client.add_gmail_labels([uid], [cfg.label])
        log.info("Forwarded %r to %s", str(msg.get("Subject", "")), ", ".join(cfg.forward_to))


def ensure_label(client: IMAPClient, cfg: Config) -> None:
    try:
        if not client.folder_exists(cfg.label):
            client.create_folder(cfg.label)
    except Exception as exc:  # Gmail auto-creates labels on STORE anyway
        log.debug("Could not pre-create label: %s", exc)


def catch_up_query(cfg: Config) -> str:
    terms = " ".join(f'"{t}"' if " " in t else t for t in cfg.required_terms)
    label = cfg.label.replace(" ", "-")
    return f"{terms} newer_than:{cfg.lookback_days}d -label:{label}"


def watch(cfg: Config) -> None:
    """One connection's lifetime. Returns/raises when the connection dies."""
    with IMAPClient(cfg.imap_host, ssl=True, timeout=60) as client:
        client.login(cfg.address, cfg.app_password)
        ensure_label(client, cfg)
        info = client.select_folder(cfg.folder)
        last_uid = int(info[b"UIDNEXT"]) - 1
        log.info("Connected to %s/%s; watching for new mail", cfg.address, cfg.folder)

        # Catch up on anything that arrived while we were offline.
        process_uids(client, cfg, client.gmail_search(catch_up_query(cfg)))

        while True:
            client.idle()
            try:
                client.idle_check(timeout=cfg.idle_seconds)
            finally:
                client.idle_done()

            # "N:*" always returns the highest UID, so filter explicitly.
            new = [u for u in client.search(["UID", f"{last_uid + 1}:*"]) if u > last_uid]
            if new:
                last_uid = max(new)
                process_uids(client, cfg, new)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    cfg = Config.from_env()
    log.info("Forwarding mail containing %s to %s", cfg.required_terms, cfg.forward_to)

    backoff = 5
    while True:
        started = time.monotonic()
        try:
            watch(cfg)
        except KeyboardInterrupt:
            return
        except (IMAPClient.Error, smtplib.SMTPException, OSError, socket.error, ssl.SSLError) as exc:
            log.warning("Connection problem: %s", exc)
        except Exception:
            log.exception("Unexpected error")
        if time.monotonic() - started > 300:
            backoff = 5  # it ran fine for a while; reset
        log.info("Reconnecting in %ss", backoff)
        time.sleep(backoff)
        backoff = min(backoff * 2, 300)


if __name__ == "__main__":
    main()
