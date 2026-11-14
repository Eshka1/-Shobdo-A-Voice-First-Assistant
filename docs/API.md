# Shobdo backend API

Backend owner: Ifreet (Eleen). Frontend owner: Eshika. This file is the contract between the two.

## Rules that apply to every route

- All bodies and replies are JSON. Send `Content-Type: application/json`.
- Every `POST` and `DELETE` needs the header `X-CSRFToken`. Get a token from `GET /api/auth/csrf-token`. **After any sign-in the old token stops working**: use the `csrf_token` that the sign-in reply returns.
- Every route except `/`, `/health` and the `/api/auth` sign-in routes needs a signed-in session. Without one the reply is `401 {"error": "Authentication required"}`.
- The user is always taken from the session. No route accepts a `user_id`.
- Unknown fields are rejected with `400`. There is no `confirmed` field anywhere: only a `pending_id` that the server issued can be confirmed.
- Errors look like `{"error": "...", "code": "..."}`. `429` means too many requests.

## Sign-in

| Method | URL | Body | Notes |
|---|---|---|---|
| GET | `/api/auth/csrf-token` | – | `{"csrf_token"}` |
| POST | `/api/auth/register` | `name`, `email`, `password` (8+ chars) | 201 |
| POST | `/api/auth/login` | `email`, `password` | `{"user", "csrf_token"}` |
| POST | `/api/auth/demo-login` | none (ignored) | `{"user", "csrf_token"}`. Works only when the server has `DEMO_LOGIN_ENABLED=true`, otherwise `404`. Signs in the shared demo user and adds 3 sample contacts and 2 sample messages the first time. |
| POST | `/api/auth/logout` | – | |
| GET | `/api/auth/me` | – | `{"user"}` or 401 |

## POST /api/voice-command

The one route for everything the user says or types. Limit: 30 per minute.

**Request**

| Field | Required | Meaning |
|---|---|---|
| `text` | yes | What was heard or typed (also "হ্যাঁ" / "না" from the Yes / No buttons). |
| `lang` | no | `"en"` or `"bn"`. Accepted for convenience; the reply always carries both languages. |
| `pending_id` | no | Send it back while a question is waiting for yes / no. `null` is fine. |
| `task` | no | Send it back when the previous reply was `clarify` and contained `task`. |
| `awaiting` | no | Send `"contact"` when the previous reply contained `"awaiting": "contact"`. |

Send at most one of `pending_id`, `task`, `awaiting`.

**Reply** (always HTTP 200 unless the request itself is invalid, or 503 if the database is down)

Every reply has `status`, `speak_bn`, `speak_en`. The same values are also sent as `state`, `message_bn`, `message_en`, so either set of names works.

| `status` | Meaning | Extra fields | What the page should do |
|---|---|---|---|
| `confirm` | Details are complete; read them back. | `pending_id`, `intent`, `expires_in_seconds` (120), `reminder` or `contact` | Speak, show Yes / No, **remember `pending_id`**. |
| `waiting` | The reply to the read-back was not a clear yes or no. | `pending_id`, `code: "unclear"` | Speak, **keep the same `pending_id`** and keep Yes / No visible. |
| `clarify` | Something is missing or unclear. | `code`, `intent`, sometimes `task`, sometimes `awaiting` and `choices` | Speak. Remember `task` / `awaiting` and send it with the next `text`. Forget `pending_id`. |
| `done` | The action happened. | `intent`, plus `reminder`, or `action: "open_dialer"` with `tel` and `contact`, or `message` | Speak. Reminder: reload the list. Call: open `tel`. |
| `cancelled` | The user said no. Nothing was done. | | Speak, forget `pending_id`. |
| `control` | Spoken repeat / slower / stop. `speak_*` are empty. | `action`: `repeat`, `slow_down` or `stop`; `pending_id` if a question is still waiting | Do exactly what the Repeat / Slower / Stop buttons do. Do not speak the empty text. If `pending_id` is present, keep it. |
| `failed` | Expired, already used, in the past, duplicate... | `code` | Speak, forget `pending_id`. |
| `unsupported` | Recognised but not available. | `intent` | Speak. |

Notes:
- While a question is waiting, saying "থামো" / "stop" counts as **no** and cancels it.
- "না, কাল সকাল ৮টায় ..." (a correction) cancels the old read-back and returns a new `confirm` with a new `pending_id`.
- A call is never placed by the server. `done` + `open_dialer` only hands over a `tel:` link; the user presses the call button in the phone's dialer.
- "মেসেজ পড়ো" / "read my messages" replies `done` with the oldest unread message in `speak_*` and marks it read. No confirmation is needed because nothing leaves the device.

### What the page needs to change (Eshika)

In `sendCommand()` in `static/js/app.js`, three things are needed. The current code already works for a complete reminder followed by yes / no.

```js
let pendingId = null;
let pendingTask = null;      // new
let awaiting = null;         // new

async function sendCommand(text) {
    const body = { text: text, lang: currentLang };
    if (pendingId) body.pending_id = pendingId;
    else if (pendingTask) body.task = pendingTask;
    else if (awaiting) body.awaiting = awaiting;

    const res = await api("/api/voice-command", "POST", body);
    // ... existing error handling ...
    const d = res.data;

    if (d.state === "control") {                 // spoken repeat / slower / stop
        if (d.action === "repeat") repeatBtn.click();
        if (d.action === "slow_down") slowBtn.click();
        if (d.action === "stop") stopBtn.click();
        return;                                  // keep pendingId as it is
    }

    pendingId = (d.state === "confirm" || d.state === "waiting") ? d.pending_id : null;
    pendingTask = d.state === "clarify" ? (d.task || null) : null;
    awaiting = d.state === "clarify" ? (d.awaiting || null) : null;

    // ... existing speak / status code ...
    if (d.state === "done" && d.action === "open_dialer") window.location.href = d.tel;
}
```

Sign-in on page load (replaces the Step 25 test login):

```js
const res = await api("/api/auth/demo-login", "POST");
if (res.ok) csrfToken = res.data.csrf_token;     // the old token is no longer valid
```

## Reminders

| Method | URL | Body | Reply |
|---|---|---|---|
| GET | `/api/reminders?status=pending` | – | `{"reminders": [{id, title, description, remind_at, status, created_at}]}`. `status` is optional: `pending`, `completed`, `cancelled`. Times are ISO strings in Dhaka time. |
| POST | `/api/reminders` | `pending_id` | Confirms a pending reminder without speech. 201. Normally not needed: saying yes through `/api/voice-command` does the same. |
| POST | `/api/reminders/<id>/complete` | – | 200, 404, or 409 if it is not pending |
| POST | `/api/reminders/<id>/cancel` | – | same |
| GET | `/api/reminders/due` | – | Reminders whose time has come and were not acknowledged. Each has `late: true/false`. Poll this (limit 30 per minute). |
| POST | `/api/reminders/<id>/ack` | – | Call after speaking a due reminder so it is not spoken again. |

## Contacts

| Method | URL | Body | Reply |
|---|---|---|---|
| GET | `/api/contacts` | – | `{"contacts": [{id, name, phone, aliases}]}` |
| POST | `/api/contacts` | `name` (1–60 letters/digits/spaces), `phone` (3–15 digits, optional `+`), optional `aliases` (max 5 names) | 201 `{"contact"}`; 409 `duplicate_contact` or `limit_reached` (50) |
| DELETE | `/api/contacts/<id>` | – | 200 or 404 |
| POST | `/api/contacts/match` | `name` | `{"result": "none" / "one" / "many", "matches": [...]}`. Finds only; never calls. `many` means ask the user. |

## Messages

These are sample messages stored inside Shobdo. The app does not read the phone's SMS, and there is no route to create a message.

| Method | URL | Body | Reply |
|---|---|---|---|
| GET | `/api/messages` | – | `{"messages": [{id, sender, text, received_at, read}]}` newest first |
| POST | `/api/messages/read` | `{}` for the oldest unread, or `{"message_id"}` | `{"message": {...} or null, "speak_bn", "speak_en"}` and marks it read. 404 for an unknown id. |

## Running

- Development: `python run.py`, then open http://127.0.0.1:5000/
- Tests: `python -m pytest -q` (uses an in-memory fake database; never touches the real one)
- Production: `waitress-serve --listen=0.0.0.0:8000 --threads=8 wsgi:app` with `FLASK_ENV=production`, a real `SECRET_KEY`, `MONGO_URI`, and `TRUST_PROXY=true` if the host puts a proxy in front. Use one process only (see `wsgi.py`).

## Known limitations

- Spoken hours as words work for "নয়টায়"-style Bangla and "nine pm"-style English. Plain "নয়টা" without the ending, and minutes as words ("সাড়ে" is supported, "nine thirty" is not), still need digits.
- Speech recognition accuracy has not been measured on real elderly speakers.
- Rate-limit counters are kept in memory: they reset on restart.
- The demo sign-in gives every visitor the same account. Turn it off (`DEMO_LOGIN_ENABLED=false`) outside the demo.
- Registration is open to anyone and has no email verification.
- Due reminders reach the page by polling, so a reminder is spoken up to ~30 seconds late and only while the page is open.
- The root `app.py` is an old test file without any protection. Do not run it; start the app with `run.py` or `wsgi.py`.
