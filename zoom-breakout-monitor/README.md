# Zoom Breakout Chat Monitor

A self-hosted web application that receives Zoom in-meeting chat webhooks,
attributes every message to the breakout room it came from, and shows all
rooms at once in a single live dashboard.

The problem it solves: when a class or workshop splits into breakout rooms, the
host can only be in one room at a time. Zoom does not surface breakout-room chat
to the host. This puts every room's chat on one screen, flags messages that
contain words like "help" or "stuck", and shows which rooms have gone quiet.

---

## Table of contents

1. [What it does](#1-what-it-does)
2. [Architecture](#2-architecture)
3. [Requirements](#3-requirements)
4. [Install with Docker](#4-install-with-docker)
5. [Development install](#5-development-install)
6. [Creating the Zoom Marketplace app](#6-creating-the-zoom-marketplace-app)
7. [Zoom scopes and permissions](#7-zoom-scopes-and-permissions)
8. [Enabling `meeting.chat_message_sent`](#8-enabling-meetingchat_message_sent)
9. [In-meeting chat / DLP prerequisites](#9-in-meeting-chat--dlp-prerequisites)
10. [Webhook endpoint configuration](#10-webhook-endpoint-configuration)
11. [Webhook URL verification](#11-webhook-url-verification)
12. [Environment variables](#12-environment-variables)
13. [Testing with simulation mode](#13-testing-with-simulation-mode)
14. [Testing with a real Zoom meeting](#14-testing-with-a-real-zoom-meeting)
15. [Security](#15-security)
16. [Privacy and retention](#16-privacy-and-retention)
17. [Troubleshooting](#17-troubleshooting)
18. [Zoom payload compatibility](#18-zoom-payload-compatibility)
19. [API reference](#19-api-reference)
20. [Running the tests](#20-running-the-tests)

---

## 1. What it does

- Receives Zoom `meeting.chat_message_sent` (and `webinar.chat_message_sent`)
  webhooks and **verifies every delivery's signature** before trusting it.
- Groups messages by `breakout_room_uuid` into stable, numbered rooms.
- Normalises Zoom's JSON into an internal `ChatMessage` model, so nothing in
  the UI depends on Zoom's wire format.
- Stores everything in SQLite (or PostgreSQL) and pushes it to connected
  dashboards over WebSocket in real time.
- Shows a responsive grid of room cards, a combined searchable message stream,
  keyword alerts, per-room activity/quiet status, and meeting history.
- Hides private/direct messages by default — their text is never written to
  disk unless you explicitly opt in.
- Requires a login. The dashboard is not publicly accessible.
- Includes a simulation mode so you can build and test the whole live dashboard
  without ever starting a Zoom meeting.

---

## 2. Architecture

```
                          Zoom cloud
                              │
                              │  HTTPS POST, HMAC-SHA256 signed
                              ▼
                  ┌───────────────────────┐
                  │  nginx  (web)         │   one origin for UI + API,
                  │  :80                  │   so the HttpOnly session
                  └───────────┬───────────┘   cookie also authenticates
                     /        │        \       the WebSocket upgrade
              static/         │/api/    \/ws
              SPA             ▼          ▼
                  ┌───────────────────────────────────┐
                  │  FastAPI backend  :8000           │
                  │                                   │
                  │  /api/zoom/webhook                │
                  │      │                            │
                  │      ├─ verify signature          │
                  │      ├─ normalise payload         │
                  │      ├─ resolve meeting + room    │
                  │      ├─ apply privacy rules       │
                  │      └─ match alert keywords      │
                  │           │            │          │
                  │           ▼            ▼          │
                  │      SQLAlchemy   ConnectionMgr   │
                  └───────────┬────────────┬──────────┘
                              │            │
                              ▼            ▼
                      SQLite volume   React dashboard
                    (or PostgreSQL)   (live over /ws)
```

Ingestion path, in one line:

```
raw bytes → verify_signature → parse_chat_event → NormalizedChatMessage
          → ingest_chat_message → database → WebSocket broadcast → UI
```

The simulator and the `tools/simulate_chat.py` CLI both feed this **same**
path, so what you test in development is what runs in production.

### Layout

```
zoom-breakout-monitor/
├── backend/
│   ├── app/
│   │   ├── main.py              app assembly, startup, middleware
│   │   ├── config.py            environment-backed settings
│   │   ├── database.py          engine/session, SQLite + PostgreSQL safe
│   │   ├── logging_config.py    structured JSON logging + redaction
│   │   ├── models/              SQLAlchemy entities
│   │   ├── schemas/             Pydantic API contract
│   │   ├── api/                 auth, webhook, meetings, rooms, settings, ws, dev
│   │   ├── services/
│   │   │   ├── zoom.py          signature verification + payload normalisation
│   │   │   ├── chat.py          ingestion, room grouping, privacy, alerts
│   │   │   ├── websocket.py     connection manager / fan-out
│   │   │   ├── serializers.py   ORM → API models
│   │   │   ├── settings_store.py
│   │   │   ├── retention.py     scheduled cleanup
│   │   │   ├── simulator.py     DEV_MODE only
│   │   │   └── zoom_api.py      optional Server-to-Server OAuth calls
│   │   └── security/            bcrypt, JWT sessions, rate limiting
│   ├── tests/                   pytest suite + sanitized webhook fixtures
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                    React + TypeScript + Vite
│   ├── src/{components,pages,hooks,services,types}
│   ├── nginx.conf
│   └── Dockerfile
├── tools/simulate_chat.py       signs and posts fake webhooks over HTTP
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## 3. Requirements

**To run it**

- A Linux host with Docker Engine 20.10+ and the Compose plugin.
- A public HTTPS URL that Zoom can reach. Zoom will not deliver to plain HTTP
  and will not deliver to a private address. In production put this behind a
  TLS-terminating reverse proxy (Caddy, Traefik, nginx) or a tunnel; for
  testing, ngrok or Cloudflare Tunnel is fine.
- A Zoom account where you can create a Marketplace app, plus (see
  [§9](#9-in-meeting-chat--dlp-prerequisites)) the in-meeting chat webhook
  capability enabled on the account.

**To develop it**

- Python 3.12+ and Node.js 20+ (22 recommended).

---

## 4. Install with Docker

```bash
git clone <your-fork> zoom-breakout-monitor
cd zoom-breakout-monitor

cp .env.example .env
$EDITOR .env          # see §12; at minimum set the three REQUIRED values

docker compose up -d
```

Open `http://<host>:8080` and sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

Two containers come up:

| Service   | What it is                                   | Exposed            |
|-----------|----------------------------------------------|--------------------|
| `backend` | FastAPI + Uvicorn                            | internal only      |
| `web`     | nginx: serves the SPA, proxies `/api` + `/ws`| `${HTTP_PORT}`:80  |

The backend is deliberately **not** published to the host. Everything goes
through nginx so the browser stays on one origin — that is what lets the
HttpOnly session cookie authenticate the WebSocket upgrade (a browser cannot
attach custom headers to one) with no CORS configuration and no token ever
exposed to page scripts.

The SQLite database lives in the named volume `chat-data`, mounted at
`/app/data`, so it survives rebuilds.

Useful commands:

```bash
docker compose logs -f backend      # structured JSON logs
docker compose ps                   # health status
docker compose down                 # stop (keeps the volume)
docker compose down -v              # stop AND DELETE ALL STORED CHAT
```

### Putting TLS in front

Bind the app to localhost and let your existing reverse proxy terminate TLS:

```dotenv
HTTP_BIND=127.0.0.1
HTTP_PORT=8080
COOKIE_SECURE=true      # only send the session cookie over HTTPS
```

Your proxy must forward `Upgrade`/`Connection` headers for `/ws` or the
dashboard will sit on "Reconnecting".

### Using a registry mirror

If your host cannot pull from Docker Hub:

```bash
BASE_REGISTRY=mirror.gcr.io docker compose build
```

### Switching to PostgreSQL

The schema uses only portable constructs. Add `psycopg[binary]` to
`backend/requirements.txt`, add a `postgres` service, and set:

```dotenv
DATABASE_URL=postgresql+psycopg://zoomchat:password@postgres:5432/zoomchat
```

Tables are created on first start. There is no migration tooling: if you
change the schema later, add Alembic.

---

## 5. Development install

Two terminals. The Vite dev server proxies `/api` and `/ws` to the backend, so
the browser stays single-origin here too.

**Backend**

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

export ZOOM_WEBHOOK_SECRET_TOKEN=any-dev-value
export ADMIN_PASSWORD=devpassword
export SESSION_SECRET=dev-session-secret
export DEV_MODE=true
export DATABASE_URL=sqlite:///data/zoomchat.db

.venv/bin/uvicorn app.main:app --reload --port 8000
```

**Frontend**

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173
```

Interactive API docs: `http://localhost:8000/api/docs`.

---

## 6. Creating the Zoom Marketplace app

1. Sign in at <https://marketplace.zoom.us/> and choose **Develop → Build App**.
2. Create a **General App** (or a **Webhook-only** app if your account still
   offers that type — this project only needs webhooks unless you want the
   optional room-name suggestions).
3. Under **Basic Information**, note the app credentials. You only need
   `ZOOM_CLIENT_ID` / `ZOOM_CLIENT_SECRET` / `ZOOM_ACCOUNT_ID` if you want the
   optional breakout-room name suggestions (see [§7](#7-zoom-scopes-and-permissions)).
4. Under **Feature → Event Subscriptions** (older UI: **Feature → Event
   Subscriptions**; some app types call it **Access → Event Subscription**):
   - Turn **Event Subscriptions** on.
   - Copy the **Secret Token** into `ZOOM_WEBHOOK_SECRET_TOKEN`. This is the
     value every delivery is signed with. It is *not* the client secret.
   - Add a subscription with your endpoint URL (see [§10](#10-webhook-endpoint-configuration)).
5. Add the event **Meeting → Start and End → In-meeting chat message sent**
   (`meeting.chat_message_sent`).
6. Validate the endpoint URL (see [§11](#11-webhook-url-verification)).
7. **Activate** the app (Local Test / Activation). An app that is built but
   never activated does not deliver events.

> Zoom changes the Marketplace UI and its app types regularly. The
> authoritative source is <https://developers.zoom.us/docs/api/webhooks/>; the
> labels above may not match your console exactly.

---

## 7. Zoom scopes and permissions

**For the chat monitor itself: no OAuth scopes are needed.** Webhooks are
authenticated by the Secret Token signature, not by an access token. Leave
`ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET` and `ZOOM_ACCOUNT_ID` blank and
everything in this README except room-name suggestions works.

What you do need is the **event subscription** itself (§8) plus the account
capability (§9).

**Optional — breakout-room name suggestions.** If you fill in the three
Server-to-Server OAuth values, the Rename dialog can offer the breakout-room
names an organiser pre-assigned on the meeting, read from
`GET /meetings/{meetingId}` → `settings.breakout_room.rooms[].name`. That
requires a meeting-read scope:

- Granular: `meeting:read:meeting:admin`
- Classic: `meeting:read:admin`

When you add the event subscription, the Marketplace console lists the scopes
that event requires for your app type. **Use the list the console shows you** —
it is authoritative and scope names have been renamed at least once (classic →
granular). If the console asks for scopes beyond the above, add exactly those.

This feature is strictly best-effort and is never on the critical path:

> **Zoom does not expose a mapping from `breakout_room_uuid` to a room name.**
> The chat webhook carries the UUID but no name, and no public endpoint
> resolves one to the other. So the app numbers rooms itself in first-seen
> order ("Breakout Room 1", "Breakout Room 2", …), keeps that mapping stable
> for the life of the meeting, and lets an administrator rename any room by
> hand. Pre-assigned names are offered as *suggestions* in the rename dialog,
> not applied automatically, because matching a name to a UUID is a judgement
> only a human can make.

---

## 8. Enabling `meeting.chat_message_sent`

In your app's **Event Subscriptions**, add the event whose slug is
`meeting.chat_message_sent`. In the console tree it appears under **Meeting**,
usually labelled something like *"In-meeting chat message sent"* or
*"Meeting chat message sent"*.

If you also want webinar chat, add `webinar.chat_message_sent` — the backend
handles both and treats them identically.

Subscribing to extra events is harmless: anything the app does not handle is
acknowledged with `200 {"received": true, "handled": false}` and ignored, so
Zoom will not mark your endpoint unhealthy.

After changing subscriptions you usually have to **re-save and re-activate**
the app before deliveries start.

---

## 9. In-meeting chat / DLP prerequisites

**This is the single most common reason the app receives nothing.**

`meeting.chat_message_sent` is not a self-service event on most accounts. It
is gated behind Zoom's **in-meeting chat DLP (Data Loss Prevention)**
capability, which must be enabled on the account by Zoom before the event will
fire — even when the subscription is configured correctly and other meeting
events (participant joined/left, meeting started/ended) arrive normally.

What this means in practice:

1. **Your Zoom plan must support it.** In-meeting chat DLP has historically
   required a paid/business-tier account and, in some cases, an add-on.
2. **Zoom must switch it on for your account.** Open a ticket with Zoom
   Support or your Zoom account manager asking them to enable
   *"In-Meeting Chat DLP"* / the *in-meeting chat message webhook events* for
   your account ID, naming `meeting.chat_message_sent`.
3. **In-meeting chat must be enabled** in Account/Group/User settings — if
   chat is turned off for the meeting, there are no messages to send.
4. The account admin may also need to enable **archiving / chat retention**
   related settings depending on how your account is provisioned.

Symptom of this not being done: the URL validation handshake succeeds, other
meeting events arrive, and `meeting.chat_message_sent` simply never fires.
No error is reported anywhere — it is silent. See
[§17](#17-troubleshooting).

Confirm the current requirements at
<https://developers.zoom.us/docs/api/meetings/events/> before filing the
ticket, as Zoom has adjusted this gating over time.

---

## 10. Webhook endpoint configuration

Endpoint URL to register with Zoom:

```
https://your-host.example.com/api/zoom/webhook
```

Requirements:

- **HTTPS with a publicly trusted certificate.** Zoom will not deliver to
  plain HTTP, to a self-signed certificate, or to an address it cannot resolve.
- Reachable from the public internet.
- Must answer the validation handshake (§11).

For local development, tunnel it:

```bash
ngrok http 8080
# then register https://<id>.ngrok-free.app/api/zoom/webhook
```

Note that the free ngrok URL changes on every restart, and you must re-validate
in the Marketplace console each time.

**Do not put the webhook path behind a login, an IP allowlist that excludes
Zoom, or basic auth.** Zoom cannot authenticate; the HMAC signature is what
proves the request came from Zoom. The endpoint is intentionally the only
unauthenticated route in the app, and it is rate-limited
(`WEBHOOK_RATE_LIMIT_PER_MINUTE`) and body-size capped.

---

## 11. Webhook URL verification

When you click **Validate** in the Marketplace, Zoom posts an
`endpoint.url_validation` event containing a `plainToken`. The endpoint must
reply with that token plus its HMAC-SHA256 using your Secret Token:

```json
{ "plainToken": "<echoed>", "encryptedToken": "<hex hmac-sha256>" }
```

This app implements that automatically. It also **verifies the signature on the
validation request itself** before answering, which Zoom's own sample does too.

You can test the handshake yourself without Zoom:

```bash
python tools/simulate_chat.py \
  --url https://your-host.example.com/api/zoom/webhook \
  --validate-only
```

It prints `URL validation handshake OK` on success and shows the server's
response otherwise.

### How delivery signatures are verified

Every delivery carries two headers:

| Header                    | Contents                                |
|---------------------------|-----------------------------------------|
| `x-zm-request-timestamp`  | epoch milliseconds                      |
| `x-zm-signature`          | `v0=<hex hmac-sha256>`                  |

The app computes:

```
message   = "v0:" + <x-zm-request-timestamp> + ":" + <raw request body>
signature = "v0=" + HMAC_SHA256(ZOOM_WEBHOOK_SECRET_TOKEN, message).hexdigest()
```

and compares it against the header in constant time. Deliveries older than
`ZOOM_WEBHOOK_MAX_AGE_SECONDS` (default 300) are rejected as replays.

> **Deliberate difference from Zoom's Node sample.** Zoom's reference receiver
> (<https://github.com/zoom/webhook-sample>) builds the signed string with
> `JSON.stringify(req.body)` — it re-serialises the *parsed* object. This app
> hashes the **raw bytes exactly as received**. Re-serialising is fragile: any
> difference in key order, unicode escaping, or whitespace silently breaks the
> hash. Raw bytes are the only correct input, and they are what Zoom signs.

---

## 12. Environment variables

`.env.example` is the authoritative, commented list. Summary:

### Required

| Variable | Purpose |
|---|---|
| `ZOOM_WEBHOOK_SECRET_TOKEN` | Secret Token from the app's Event Subscription page. Without it **every** delivery is rejected. |
| `ADMIN_PASSWORD` | Password for the local admin account. Without it nobody can sign in. |
| `SESSION_SECRET` | Signs session cookies. If blank, a random key is generated per process and every restart signs everyone out. Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |

### Zoom (optional)

| Variable | Default | Purpose |
|---|---|---|
| `ZOOM_CLIENT_ID` / `ZOOM_CLIENT_SECRET` / `ZOOM_ACCOUNT_ID` | empty | Server-to-Server OAuth, **only** for breakout-room name suggestions. |
| `ZOOM_WEBHOOK_MAX_AGE_SECONDS` | `300` | Replay window. `0` disables the check. |
| `ZOOM_API_BASE_URL` | `https://api.zoom.us/v2` | |

### Authentication

| Variable | Default | Purpose |
|---|---|---|
| `ADMIN_USERNAME` | `admin` | |
| `SESSION_TTL_HOURS` | `12` | |
| `COOKIE_SECURE` | `false` | Set `true` once served over HTTPS. |
| `COOKIE_SAMESITE` | `lax` | |

### Database

| Variable | Default |
|---|---|
| `DATABASE_URL` | `sqlite:///data/zoomchat.db` |

### Privacy

| Variable | Default | Purpose |
|---|---|---|
| `DISPLAY_PRIVATE_MESSAGES` | `false` | See [§16](#16-privacy-and-retention). Read this before changing it. |
| `CHAT_RETENTION_DAYS` | `30` | `0` keeps messages indefinitely. |
| `RETENTION_SWEEP_INTERVAL_MINUTES` | `60` | |
| `STORE_RAW_WEBHOOK_PAYLOAD` | `true` | Keep the original delivery for debugging. Never served through the API. Private messages are exempt regardless. |

### Behaviour / operations

| Variable | Default | Purpose |
|---|---|---|
| `DEV_MODE` | `false` | Exposes simulation. **Must be false in production.** |
| `LOG_LEVEL` | `INFO` | `DEBUG` adds message previews. |
| `INACTIVITY_WARNING_MINUTES` | `5` | Default quiet-room threshold; editable in the UI afterwards. |
| `MAX_REQUEST_BYTES` | `1048576` | |
| `HTTP_PORT` / `HTTP_BIND` | `8080` / `0.0.0.0` | Where the dashboard is published. |
| `CORS_ALLOW_ORIGINS` | empty | Only needed for a split-origin deployment. Accepts a comma-separated list or a JSON array. |
| `RATE_LIMIT_ENABLED` | `true` | |
| `LOGIN_RATE_LIMIT_PER_MINUTE` | `10` | |
| `WEBHOOK_RATE_LIMIT_PER_MINUTE` | `600` | |

---

## 13. Testing with simulation mode

Simulation lets you exercise the entire live dashboard — many rooms, live
messages, alerts, search, quiet-room detection — with no Zoom meeting.

It is gated on `DEV_MODE=true`. When `DEV_MODE=false` the `/api/dev/*` router
is **not mounted at all**: the paths return 404, they do not appear in the
OpenAPI document, and the simulator refuses to start even if called directly.

Set `DEV_MODE=true` in `.env`, then `docker compose up -d`.

### Option A — from the dashboard

**Settings → Simulation (developer mode)**. Choose rooms, message count and
interval, and press **Start simulation**. Every room is seeded immediately, so
the grid fills at once; messages then trickle in at your chosen interval.
Open the monitor and watch them arrive live.

### Option B — the CLI tool

`tools/simulate_chat.py` posts to the real webhook endpoint over HTTP with
**real Zoom-style signatures**, so it also exercises signature verification,
raw-body handling and the parser. It has no dependencies beyond the standard
library.

```bash
# Reads ZOOM_WEBHOOK_SECRET_TOKEN from the environment or ./.env
python tools/simulate_chat.py

# A bigger, faster class
python tools/simulate_chat.py --rooms 8 --participants 4 --messages 100 --interval 0.5

# Stream forever until Ctrl-C
python tools/simulate_chat.py --messages -1

# Include private messages, to see the privacy filter working
python tools/simulate_chat.py --private-chance 0.3

# Point at a deployed instance
python tools/simulate_chat.py --url https://your-host.example.com/api/zoom/webhook

# Just check the validation handshake
python tools/simulate_chat.py --validate-only
```

`--help` lists every flag.

Because the CLI goes through the public endpoint, it works against a running
container without `DEV_MODE` — it is a real client, not a back door. Keep it
pointed at test deployments.

---

## 14. Testing with a real Zoom meeting

1. Confirm the account prerequisites in [§9](#9-in-meeting-chat--dlp-prerequisites).
   Skipping this is the usual cause of "nothing happens".
2. Deploy with a public HTTPS URL and register it (§10), validate it (§11), and
   **activate** the app.
3. Set `DEV_MODE=false` and restart so simulation is not exposed.
4. Start a meeting from an account covered by the app's installation.
5. Send a chat message **in the main meeting first**. It should appear in the
   dashboard under "Main Meeting" within a second or two. If this does not
   work, breakout rooms will not either — fix this first.
6. Open **Breakout Rooms**, create two or three, and join participants.
7. Send a message inside each breakout room. Each one appears as its own card,
   numbered in the order it first spoke.
8. Send something containing "help" or "stuck" and confirm it is highlighted
   and the room's alert counter increments.
9. Rename a room with the ✎ button to whatever your rooms are actually called.

Watch the logs while you do this:

```bash
docker compose logs -f backend | grep -E "zoom_webhook_received|chat_message_stored|new_breakout_room_detected|webhook_verification_failed"
```

---

## 15. Security

This application can expose student or employee communications. It is built
accordingly.

**Webhook authenticity**
- HMAC-SHA256 signature verification on every delivery, compared in constant
  time, computed over raw body bytes (§11).
- Replay window on `x-zm-request-timestamp`.
- Requests are rejected *before* the body is parsed or anything is stored.
- No secret token configured ⇒ everything is rejected (fail closed).

**Authentication**
- The dashboard is not publicly accessible; every API route except
  `/api/zoom/webhook`, `/api/health` and `/api/auth/*` requires a session.
- Passwords are bcrypt-hashed. Plaintext is never stored, and a login attempt
  for a non-existent user still performs a bcrypt comparison so timing does not
  leak which usernames exist.
- Sessions are JWTs in an **HttpOnly, SameSite=Lax** cookie, never in
  `localStorage`. Page scripts cannot read the session, so an XSS bug cannot
  steal it.
- Changing `ADMIN_PASSWORD` rotates a token version that invalidates every
  session issued against the old password.
- WebSockets authenticate with the same cookie and close with policy-violation
  1008 when it is missing or expired.

**Adding SSO/OIDC later**: the admin account is a real database table, and
every route depends on a `CurrentUser` produced by one dependency
(`app/api/deps.py`). An OIDC callback that resolves a user and issues the same
cookie slots in without touching any downstream route.

**Input and transport**
- Pydantic validates every request body and query parameter.
- All SQL goes through SQLAlchemy with bound parameters; search terms are never
  interpolated into SQL. (There is a test that tries to drop the table.)
- React escapes all rendered text; message content is never injected as HTML.
- Request bodies are capped (`MAX_REQUEST_BYTES`, and `client_max_body_size` in
  nginx).
- Per-IP sliding-window rate limits on login and on the webhook endpoint.
- `X-Content-Type-Options`, `X-Frame-Options: DENY` and `Referrer-Policy` are
  set by both the app and nginx.
- CORS is **off** by default — the shipped deployment is single-origin.
- The backend container runs as an unprivileged user (uid 10001).

**Known limits, stated plainly**
- Rate limiting is in-process. Running multiple backend replicas would need a
  shared store (Redis); the limits would otherwise apply per replica.
- `X-Forwarded-For` is trusted for client identity. That is correct behind the
  bundled nginx; behind an untrusted proxy the header is spoofable.
- There is one admin account. There are no roles and no per-meeting access
  control: anyone who can sign in sees every stored meeting.
- TLS is not terminated by this stack. Put it behind a TLS proxy and set
  `COOKIE_SECURE=true`.

**Secrets**
- Everything comes from environment variables. Nothing is hard-coded.
- `.env` is in `.gitignore`; only `.env.example`, which contains no values, is
  committed.

---

## 16. Privacy and retention

**Read this before deploying.** Running this application means recording
participants' chat messages. Depending on where you are and who your
participants are, that can carry legal obligations (FERPA, GDPR, works-council
agreements, institutional policy) and, at minimum, an obligation to tell people
it is happening. Zoom shows participants that chat may be archived, but it does
not tell them a third-party dashboard is aggregating it.

### Private messages

`DISPLAY_PRIVATE_MESSAGES` defaults to **false**, and that default is strong:

- A private/direct message is recorded as **metadata only**. The message text
  is never written to the database, never included in the retained raw payload,
  never matched against alert keywords, and never broadcast over WebSocket.
- It is not counted in room or meeting totals, so the numbers on screen never
  hint at hidden content.
- Because the text was never stored, turning the setting on later does **not**
  retroactively reveal older private messages. This is intentional.

Classification errs toward privacy: a message is treated as private unless its
`recipient_type` is explicitly a broadcast value (`everyone`, `all`, …). A
misclassified public message is merely hidden; a misclassified private one
would be exposed.

The switch is **environment-only**. It is returned read-only by
`GET /api/settings` and cannot be changed through the API or the UI — enabling
private-message monitoring requires an explicit environment change and a
restart, so it can never be flipped from a browser session. When it is on, a
warning banner appears on the monitor page and in Settings. It is never
silently enabled.

### Retention

`CHAT_RETENTION_DAYS` (default 30) deletes messages older than the window. A
background sweep runs every `RETENTION_SWEEP_INTERVAL_MINUTES` and re-syncs
per-room counters afterwards. `0` keeps messages indefinitely — choose that
deliberately.

Meeting and room records outlive their messages so history stays navigable;
delete the meeting to remove those too.

### Deleting data

- **Meeting history → Delete** removes one meeting and every room and message
  under it. It requires typed confirmation in the UI and `confirm=true` on the
  API. It cannot be undone.
- `docker compose down -v` destroys the volume and therefore everything.

### Logging

Structured JSON logs record *that* a message arrived — meeting, room number,
whether it alerted, whether it was private — but **never the message text at
INFO level**. Content appears only at `LOG_LEVEL=DEBUG`, and even then only as
a truncated preview. Do not run production at DEBUG.

---

## 17. Troubleshooting

### The webhook URL does not validate

- Check that `ZOOM_WEBHOOK_SECRET_TOKEN` is the **Secret Token** from the Event
  Subscription page, not the Client Secret or the Verification Token.
- Restart after changing `.env` — the value is read at startup:
  `docker compose up -d --force-recreate backend`.
- Test it yourself, which removes Zoom from the equation:
  ```bash
  python tools/simulate_chat.py --url https://your-host/api/zoom/webhook --validate-only
  ```
- Confirm the URL ends in `/api/zoom/webhook` and is reachable from outside.
  `curl -i https://your-host/api/zoom/webhook` (a GET) should return
  **405 Method Not Allowed** — which proves the route exists and only accepts
  POST. **404** means nginx is not routing `/api` or the path is wrong;
  connection refused or a TLS error means Zoom cannot reach the host either.
  An unsigned POST should return **401**:
  ```bash
  curl -i -X POST -H 'Content-Type: application/json' -d '{}' \
    https://your-host/api/zoom/webhook
  ```
- Zoom requires a publicly trusted certificate. Self-signed will fail.
- Look for `webhook_verification_failed` in the logs — the `reason` field says
  exactly which check failed.

### No `meeting.chat_message_sent` events arrive

Work through this in order:

1. **Account capability.** This is the most likely cause — see
   [§9](#9-in-meeting-chat--dlp-prerequisites). If other events arrive and only
   chat events do not, it is almost certainly this. Open a Zoom support ticket.
2. **App activated?** Building and saving is not enough.
3. **Event actually subscribed?** Re-open Event Subscriptions and confirm
   `meeting.chat_message_sent` is listed, then re-save.
4. **In-meeting chat enabled** in Account/Group/User settings?
5. **Right account?** A webhook-only or account-level app only receives events
   for meetings on the account it is installed on.
6. **Is anything arriving at all?** `docker compose logs -f backend | grep zoom_webhook_received`.
   If that is silent, Zoom is not reaching you — check DNS, TLS and firewall.
   If it logs but nothing is stored, look for `webhook_verification_failed` or
   `chat_payload_unparseable`.
7. **Prove the pipeline independently** by running
   `python tools/simulate_chat.py` against the same URL. If simulated messages
   appear in the dashboard, the application is fine and the problem is on the
   Zoom side.

### Main-meeting chat works but breakout rooms do not

- Breakout-room chat is a distinct Zoom feature; confirm chat is permitted
  *inside* breakout rooms in the meeting's chat settings.
- Check whether the deliveries arrive at all:
  ```bash
  docker compose logs backend | grep new_breakout_room_detected
  ```
  If breakout messages arrive but carry no room identifier, they are grouped
  under "Breakout Room (unidentified)" — see the next item.
- If breakout messages land under **Main Meeting**, Zoom sent no
  `breakout_room_uuid` and no breakout context. Set `LOG_LEVEL=DEBUG`,
  reproduce, and inspect the stored raw payload (see below). Then open
  [§18](#18-zoom-payload-compatibility).

### `breakout_room_uuid` is missing

The app handles this rather than dropping the message:

- If Zoom says the message came from a breakout room but does not name which
  one, it is grouped into a room shown as **"Breakout Room (unidentified)"**
  and a `breakout_room_uuid_missing` warning is logged. You will see the
  traffic, you just cannot tell the rooms apart.
- If there is no breakout indication at all, the message is treated as
  main-meeting chat.

To see what Zoom actually sent (requires `STORE_RAW_WEBHOOK_PAYLOAD=true`):

```bash
docker compose exec backend python - <<'PY'
from app.database import SessionLocal
from app.models import ChatMessage
import json
s = SessionLocal()
m = s.query(ChatMessage).order_by(ChatMessage.received_at.desc()).first()
print(json.dumps(m.raw_payload, indent=2))
PY
```

If the UUID is present under a key this app does not look for, add it to the
candidate list in `backend/app/services/zoom.py` (`parse_chat_event`) — the
parser is built to be extended that way. Please also report it, since it means
Zoom's schema has moved.

### The dashboard shows "Disconnected" or "Reconnecting"

- The socket reconnects automatically with exponential backoff, and immediately
  when the tab becomes visible or the network returns. Brief "Reconnecting" is
  normal after a laptop wakes up.
- Persistent **Disconnected** usually means the session expired — reload; you
  should get the login page. The client deliberately stops retrying on a 1008
  close rather than looping.
- If you added your own reverse proxy, it must forward WebSocket upgrades:
  ```nginx
  proxy_http_version 1.1;
  proxy_set_header Upgrade $http_upgrade;
  proxy_set_header Connection "upgrade";
  proxy_read_timeout 3600s;
  ```
  Without `proxy_read_timeout` the socket is killed after ~60s and the badge
  flaps.
- Check the backend is up: `docker compose ps` and `curl http://localhost:8080/api/health`.

### The container cannot write the SQLite database

Symptom: `sqlite3.OperationalError: unable to open database file`, or the
backend restart-loops.

- The image creates `/app/data` owned by uid 10001 and the compose file mounts
  a **named volume** over it, which inherits that ownership. This works out of
  the box. Verify:
  ```bash
  docker compose exec backend sh -c 'id && ls -la /app/data'
  ```
- If you replaced the named volume with a **bind mount**, the host directory is
  not chowned for you:
  ```bash
  mkdir -p ./data && sudo chown -R 10001:10001 ./data
  ```
- If you changed `DATABASE_URL`, keep the path inside `/app/data`. A path
  elsewhere in the image is not writable by the unprivileged user.
- SELinux hosts may need `:z` on the volume mount.
- Out of disk on the Docker host will present the same way; check `df -h`.

### "Too many requests" on the login page

`LOGIN_RATE_LIMIT_PER_MINUTE` (default 10 per IP) has kicked in. Wait a minute,
or raise it in `.env`. If it triggers unexpectedly, check that
`X-Forwarded-For` is being set correctly — otherwise every user may look like
one IP.

### Everyone is signed out after a restart

`SESSION_SECRET` is not set, so a random key is generated per process. The log
line `session_secret_not_configured` says so at startup. Set it.

### Simulation controls are missing

`DEV_MODE` is false. That is the production default and the routes genuinely do
not exist. Set `DEV_MODE=true` and restart.

---

## 18. Zoom payload compatibility

**Please read this if a field is not being picked up.**

Zoom's webhook schemas are versioned and have changed shape before. The build
environment used to develop this application could not reach
`developers.zoom.us` (blocked by an egress policy), so the exact nesting of
every field in `meeting.chat_message_sent` could not be pinned against the
official reference.

What that means in practice, stated honestly:

**Verified against a primary source**
- The signature algorithm, header names, and the `endpoint.url_validation`
  handshake, all against Zoom's own reference receiver at
  <https://github.com/zoom/webhook-sample>.
- That `breakout_room_uuid` is the identifier Zoom uses for a breakout room
  instance in meeting webhooks.
- That `meeting.chat_message_sent` is gated behind the in-meeting chat DLP
  account capability.

**Handled defensively rather than assumed**

No field names were invented. Instead, `parse_chat_event` in
`backend/app/services/zoom.py` searches each field at **every documented and
reported location**, most specific first — inside `payload.object.chat_message`,
then `payload.object`, then `payload` — and takes the first non-empty hit:

| Internal field | Keys searched |
|---|---|
| `meeting_uuid` | `uuid`, `meeting_uuid` |
| `zoom_meeting_id` | `id`, `meeting_id` |
| `breakout_room_uuid` | `breakout_room_uuid`, `breakout_room_id`, `breakout_uuid` |
| `message` | `message_content`, `message`, `content`, `text` |
| `sender_name` | `sender_name`, `sender`, `display_name`, `user_name` |
| `sender_id` | `sender_session_id`, `sender_id`, `sender_participant_id`, `participant_user_id`, `sender_user_id` |
| `recipient_type` | `recipient_type`, `receiver_type`, `to_type` |
| `recipient_name` | `recipient_name`, `receiver_name`, `to_name` |
| `recipient_id` | `recipient_session_id`, `recipient_id`, `receiver_id`, `to_id` |
| `sender_context` | `sender_context`, `source_context`, `context` |
| `recipient_context` | `recipient_context`, `target_context` |
| `timestamp` | `date_time`, `timestamp`, `sent_time`, `send_time`, then `event_ts` |
| `zoom_message_id` | `message_id`, `msg_id` |

Anything not found stays `null` — nothing is fabricated. The complete original
delivery is kept in `raw_payload` (unless disabled, and never for private
messages) so a real capture can always be inspected, and it is never served
through the API.

Timestamps are accepted as ISO-8601 (with or without `Z`) or as epoch
seconds/milliseconds.

**If Zoom's current documentation differs from any of this**, follow the
documentation: add the key to the relevant tuple in `parse_chat_event`, add a
fixture under `backend/tests/fixtures/`, and the rest of the application needs
no changes — it depends only on the normalised model, never on Zoom's wire
format. That decoupling is the point.

---

## 19. API reference

Interactive docs: `/api/docs`. OpenAPI JSON: `/api/openapi.json`.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/api/zoom/webhook` | signature | Zoom deliveries and URL validation |
| `POST` | `/api/auth/login` | — | Sign in; sets the session cookie |
| `POST` | `/api/auth/logout` | — | Clear the session |
| `GET` | `/api/auth/session` | — | Probe: is there a valid session? |
| `GET` | `/api/meetings` | ✓ | List meetings |
| `GET` | `/api/meetings/current` | ✓ | Most recently active meeting, or `null` |
| `GET` | `/api/meetings/{meeting_uuid}` | ✓ | Meeting with rooms and recent messages |
| `GET` | `/api/meetings/{meeting_uuid}/rooms` | ✓ | Rooms for a meeting |
| `GET` | `/api/meetings/{meeting_uuid}/messages` | ✓ | Combined feed; filter by `room_id`, `sender`, `q`, `alerts_only`, `since`, `until`, `limit`, `offset` |
| `GET` | `/api/meetings/{meeting_uuid}/room-name-suggestions` | ✓ | Optional pre-assigned names from Zoom |
| `DELETE` | `/api/meetings/{meeting_uuid}?confirm=true` | ✓ | Delete a meeting and all its data |
| `GET` | `/api/rooms/{room_id}` | ✓ | One room |
| `PATCH` | `/api/rooms/{room_id}` | ✓ | Rename (`custom_name: null` restores the generated name) |
| `GET` / `PUT` | `/api/settings` | ✓ | Alert keywords, notifications, activity threshold |
| `GET` | `/api/health` | — | Liveness |
| `WS` | `/ws` | cookie | All meetings |
| `WS` | `/ws/meetings/{meeting_uuid}` | cookie | One meeting |
| `GET`/`POST` | `/api/dev/simulation*` | ✓ + `DEV_MODE` | Simulation control |

**Meeting identifiers.** Zoom meeting UUIDs are base64 and routinely contain
`/`, `+` and `=`, which do not survive a URL path cleanly — ASGI servers
percent-decode the path before routing, so an encoded slash reappears as a path
separator. Every route therefore accepts **either** the internal meeting `id`
(hex, always URL-safe) **or** the Zoom meeting UUID. The frontend uses the
internal id.

**WebSocket events** are `{"type": ..., "data": ...}`:

`hello`, `chat_message` (`{room, message}`), `room_created`, `room_updated`,
`meeting_created`, `meeting_deleted`, `settings_updated`, `simulation_status`,
`pong`. The client sends `"ping"` as a keepalive.

---

## 20. Running the tests

```bash
cd backend
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

158 tests covering webhook signature verification (valid, invalid, tampered,
replayed, missing headers, missing secret), the URL-validation handshake,
payload parsing including alternate field layouts and missing breakout UUIDs,
`breakout_room_uuid` grouping and stable room numbering, meeting and room
creation, message persistence and de-duplication, keyword alert matching,
private-message filtering in both modes, retention purging, configuration
parsing, REST authentication and authorization, and WebSocket distribution.

Sanitized webhook fixtures live in `backend/tests/fixtures/`.

Frontend:

```bash
cd frontend
npm run typecheck
npm run build
```
