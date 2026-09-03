# Device testing — what to do on your end

Everything an emulator can answer already passes: 17/17 offline scenarios,
11/11 installability criteria, Lighthouse 100/100/100/100. What follows is the
part only a real phone settles.

---

## Read this first

**`http://192.168.x.x` is not a secure context, and that breaks more than
`crypto.randomUUID()`.**

Browsers gate a set of APIs on a secure context — HTTPS, or `localhost`. A
plain LAN address is neither.

| API | Gated? | Consequence on `http://192.168.x.x` |
|---|---|---|
| **Service Worker** | **yes** | **No offline shell. Not installable. Reload or deep-link while offline gives the browser's error page.** |
| `crypto.randomUUID()` | yes | Already handled — `lib/uuid.ts` falls back |
| `navigator.storage.persist()` | yes | Not used |
| Geolocation | yes | Not used |
| IndexedDB | no | The outbox works |
| localStorage | no | The cached day works |
| `crypto.getRandomValues()` | no | Which is why the UUID fallback works |

So over a plain LAN address the app runs and status updates still queue — but
the installable, offline-capable PWA is not what you would be testing.
**Don't evaluate offline that way and conclude it's broken.**

---

## Setup — Android with a USB cable (recommended)

`adb reverse` makes the phone treat your machine's ports as its own
`localhost`, which **is** a secure context. No CORS change, no
`NEXT_PUBLIC_API_BASE` change, no `DISPATCH_TOKEN` needed — the phone uses
exactly the URLs your desktop uses.

```bash
adb reverse tcp:3002 tcp:3002      # the PWA
adb reverse tcp:8000 tcp:8000      # the API

cd mobile && npm run build && npm start
```

The production build matters: the service worker only registers there.

On the phone, open **`http://localhost:3002`**. Sign in with a code from
`http://localhost:3000/access` on your desktop.

## Setup — any device, including iOS

Needs a real HTTPS origin. A tunnel is easiest:

```bash
cloudflared tunnel --url http://localhost:3002    # the PWA
cloudflared tunnel --url http://localhost:8000    # the API
```

Then, because the phone is now a genuinely different origin:

```bash
# .env
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,https://<pwa-tunnel-host>
NEXT_PUBLIC_API_BASE=https://<api-tunnel-host>
DISPATCH_TOKEN=<see below>

docker compose up -d api mobile     # restart alone will NOT reload env
curl localhost:8000/health/config   # confirm what the process actually loaded
```

### `DISPATCH_TOKEN` becomes mandatory here

The moment `CORS_ORIGINS` trusts a non-local origin, every dispatcher route
answers **503** without a token. That's deliberate — failing closed beats a
warning in a log, and the condition that makes the hole real is exactly the
condition that makes the token required.

```bash
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

Put it in `.env`. The console asks for it once and remembers it.
`/health/config` reports `dispatch_auth` as `on` / `off` /
`REQUIRED-BUT-UNSET` — readable *without* the token on purpose.

The technician app never needs it. A phone holds its own token and must never
be given the dispatch secret.

---

## What to check

### 1. Install to the home screen
Chrome menu → "Add to Home screen" / "Install app". Open it from the icon.
**Expected:** no address bar, no browser chrome, the blue pin icon.

### 2. Real airplane mode
Not devtools throttling — toggle the actual radio.

Do a full pass on one job: **On my way → Arrived → Complete** (fill the form,
take a real photo).

**Expected:** every tap registers, the screen updates, the strip reads
`Offline · updated HH:MM · N waiting to send`. Sign out is greyed out.

Turn the radio back on. **Expected:** within a few seconds the strip goes
silent and sign-out re-enables.

### 3. Force-quit with work queued
Queue something offline, swipe the app away, reopen.
**Expected:** still there, still counted.

### 4. Lock the phone mid-queue — *this is the Background Sync question*
Queue work with the radio off. Restore signal. **Lock the phone and leave it
5–10 minutes without opening the app.** Then check the server:

```bash
docker compose exec db psql -U waypoint -d waypoint \
  -c "SELECT job_id, status, recorded_at FROM job_status_events ORDER BY recorded_at DESC LIMIT 5;"
```

| What you see | What it means |
|---|---|
| Queue drained while locked | The page stayed alive. Nothing to do. |
| Still waiting; drains the moment you unlock | Status quo working as designed. See below. |
| Still waiting *after* unlocking too | A bug in the syncer, not a missing feature. Tell me. |

**Only the middle outcome makes Background Sync worth considering**, and only
if the delay is long enough to matter to dispatch. Worth knowing: a delayed
report is not a lost one, and phase 9 re-plans off `occurred_at` (when the
technician tapped) rather than arrival time — so a report that lands an hour
late still moves the schedule around the right moment.

If it does matter, the cheaper fix is probably `periodicSync` or a wake lock,
not moving the whole queue into the service worker.

### 5. Sunlight
Take it outside at midday. This is the one thing no amount of indoor testing
settles, and the whole visual design — light ground, full contrast, no dimmed
rows — is a bet on it.

### 6. Navigate and Call
Tap **Navigate** on a job. **Expected on Android:** the app chooser appears
with Waze and Google Maps, and the destination is right. Tap the phone icon —
the dialler should open with the customer's number pre-filled.

### 7. Gloves
If you have work gloves, try the whole flow wearing them. Every tap target is
≥ 68px for this reason; that number is a guess until somebody checks it.

---

## What to send back

- Which outcome you got for **#4** (the Background Sync decision hinges on it)
- Anything in **#5** that was hard to read
- Whether **#6** opened the app you expected
- Any tap that needed a second attempt

---

## Reproducing the automated checks

```bash
cd mobile && npm run build && npm start

npm test          # 51 unit tests: outbox, drain, cache, acks
npm run offline   # 17 offline scenarios, real browser + service worker
npm run audit     # Lighthouse + 11 installability criteria
```

`npm run offline` needs `WAYPOINT_DISPATCH_TOKEN` set if the stack is locked;
it will tell you so rather than hanging.
