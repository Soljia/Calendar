# Instant Venmo forwarder (IMAP IDLE)

A small always-on worker that forwards Gmail messages containing **"paid you"**
and **"Venmo"** to your 4 recipients **within seconds** of their arrival.
Recipients don't have to accept, confirm or sign up for anything.

## How it works

```
Venmo ──email──▶ Gmail ──IMAP IDLE push──▶ forwarder.py ──SMTP──▶ 4 recipients
                                              │
                                              └─ adds Gmail label "Venmo-Forwarded"
```

- The worker keeps an IMAP **IDLE** connection open, so Gmail tells it about
  new mail immediately (no polling interval).
- Each new message is checked for every term (subject + text + HTML,
  case-insensitive), forwarded through Gmail SMTP from your own address, then
  labeled **Venmo-Forwarded** so it's never sent twice.
- On startup and after any reconnect it catches up on unlabeled matches from
  the last 2 days, so mail that arrived while it was down still gets forwarded.
- Loop-safe: it ignores mail you sent and its own forwards.
- It fetches with `BODY.PEEK`, so messages stay unread in your inbox.

## 1. Gmail prep (one time)

1. Turn on **2-Step Verification**: <https://myaccount.google.com/security>.
2. Create an **App Password**: <https://myaccount.google.com/apppasswords>.
   Name it "Venmo forwarder" and copy the 16-character code.
3. IMAP is always on for personal Gmail accounts. On a Workspace account,
   an admin may need to allow IMAP and App Passwords.

## 2. Configure

```bash
cp .env.example .env
# edit .env: GMAIL_ADDRESS, GMAIL_APP_PASSWORD, FORWARD_TO
```

## 3. Run it

**Locally (to test):**

```bash
pip install -r requirements.txt
set -a; source .env; set +a
python forwarder.py
```

Send yourself a test email with the subject `Test paid you via Venmo`; it
should be forwarded within a few seconds and you'll see `Forwarded ...` in the log.

**With Docker (anywhere):**

```bash
docker compose up -d --build
docker compose logs -f
```

## 4. Hosting options

This is a background worker with no web port, so it needs a host that keeps a
process running 24/7 (not a serverless function).

| Host | Cost | Notes |
|---|---|---|
| Google Cloud `e2-micro` VM | Free tier (US regions) | Install Docker, clone, `docker compose up -d` |
| Oracle Cloud Always Free VM | Free | Same as above |
| Railway / Render background worker | ~$5–7/mo | Deploy this folder from GitHub; set the env vars in their dashboard |
| Raspberry Pi / home PC | Free | Fine if it's always on |

Set the same environment variables from `.env.example` in whatever host you pick.

## Limits and notes

- Gmail allows roughly 500 recipients/day for personal accounts, which is
  about 125 forwarded Venmo emails a day to 4 people.
- The App Password gives full mailbox access. Store it only in the host's
  secret/env settings, and revoke it at
  <https://myaccount.google.com/apppasswords> if it's ever exposed.
- Anyone can send you an email containing "paid you" and "Venmo". To forward
  only real Venmo mail, set `ALLOWED_SENDERS=venmo.com`.
- If you run this, turn off the Apps Script version (`teardown`) so people
  don't get duplicates.

## Tests

```bash
python -m unittest -v test_forwarder
```
