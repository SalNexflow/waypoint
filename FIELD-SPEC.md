# Waypoint Field — Technician PWA

The technician-facing half of Waypoint. Runs on a phone, shows one person their
own day, and sends back what actually happened.

This is an addition to the existing repo, not a new project. It shares the
database, the job model and the API. The solver, routing and dispatcher console
are untouched.

## Why it exists

Two reasons, and the second is the one that matters technically.

**For the technician:** their day, in order, on their phone at 7am. No morning
call to dispatch to find out where they're going, no wrong-part trips, and when
dispatch reassigns something they see it instead of being rung about it.

**For the system:** status updates are the input re-optimisation has been
missing. Phase 9 currently assumes jobs finish when predicted. Once a technician
marks a job done at 10:15 that was scheduled to end at 10:00, the system knows
the day is running fifteen minutes behind and can re-solve before it cascades.
Without this, mid-day re-optimisation is guessing.

## Constraint that shapes everything

This gets used standing outside a building, in sunlight, one-handed, sometimes
with gloves, sometimes in a basement with no signal.

- One screen, one job, one action. If a technician has to think about the
  interface, it is wrong.
- Big touch targets, high contrast, no thin greys.
- Offline-first. Status updates queue locally and sync when the connection
  returns. A technician must never be blocked by a dead zone.
- Fast. It opens, it shows the next job. No spinner on the critical path.

## Where it lives

```
web/        dispatcher console (existing, unchanged)
mobile/     technician PWA (new)
api/        shared backend, new routes only
```

`mobile/` is its own Next.js app in its own container on its own port. It does
not import from `web/`.

## Stack

- Next.js 16, React 19, TypeScript
- Tailwind
- PWA: manifest, service worker, installable to home screen
- IndexedDB for the offline queue
- No state library. React state and a small sync module is enough for four
  screens.

## Screens

Four. No more.

### 1. Today

The landing screen and the one they look at most.

- One line of context at top: "6 jobs · finish around 16:40". The finish estimate
  is what they actually want to know.
- Vertical list of their jobs in visit order.
- Each row: time, customer name, area, status dot.
- The current job is visually dominant — larger, coloured, pinned at top.
  Everything after it is dimmed and smaller. Completed jobs collapse.

### 2. Job detail

- Customer name, full address, time window, duration estimate
- What the job is: service type, fault description
- Parts required
- Note from dispatcher or customer, if any
- Phone icon: calls the customer
- Navigate button: hands off to Waze or Google Maps via geo URI. Do not build
  turn-by-turn.
- One large action button at the bottom, thumb-reachable, changing with state:
  **On my way** -> **Arrived** -> **Complete**

### 3. Complete

Reached by tapping Complete. Deliberately short — if this takes too long people
stop filling it in and the data becomes worthless.

- Parts actually used, prefilled with what was planned
- Notes field
- Optional photo
- Done

### 4. Schedule changed

Not navigated to — it interrupts. When dispatch reassigns or cancels something
belonging to this technician, a full-screen notice: what changed, which job, new
time. One button to acknowledge.

This screen replaces the phone call.

## Explicitly out of scope

- No map view of the whole day. They need the next address, not the route shape.
- No chat. It becomes a support channel someone has to staff.
- No timesheet, no admin, no reporting.
- No settings beyond logout.

## Auth

The dispatcher console has none, which is fine on localhost. This cannot ship
without it — Ahmad must see Ahmad's day and not everyone's.

Minimum viable: a per-technician token issued by the API, stored in the client,
sent as a bearer header. Every `/field/*` route resolves the technician from the
token and scopes the query to them. A technician requesting another technician's
job gets 404, not 403 — do not confirm the job exists.

Do not build a login form with passwords. A magic link or a dispatcher-issued
code is enough at this stage and is one less thing to get wrong.

## New API routes

All under `/field`, all scoped to the authenticated technician.

- `GET /field/today` — their assignments for the current date: jobs in visit
  order, with customer, address, coordinates, window, duration, parts, notes,
  and current status
- `POST /field/jobs/{id}/status` — `{status, at}` where status is
  `en_route | arrived | complete`, and `at` is a client timestamp
- `POST /field/jobs/{id}/complete` — `{parts_used, notes, photo?}`
- `GET /field/changes` — unacknowledged schedule changes for this technician
- `POST /field/changes/{id}/ack`

Status updates carry a client-generated UUID for idempotency. A queued update
retried after reconnection must not create a duplicate.

The `at` timestamp is the client's, not the server's. A job completed offline at
10:15 and synced at 11:40 happened at 10:15, and re-optimisation needs the real
time.

## Data model additions

**job_status_events** — id (client UUID), job id, technician id, status,
occurred_at, recorded_at, synced

**job_completions** — job id, parts_used JSONB, notes, photo key, completed_at

**schedule_changes** — id, technician id, job id, kind, detail, created_at,
acknowledged_at

Keep events append-only. The current status of a job is derived from its latest
event, not stored as a mutable column. That way an out-of-order sync cannot
overwrite a later state with an earlier one.

## Offline behaviour

The hard part, and the reason this is worth building carefully.

- `GET /field/today` result is cached. Opening the app with no signal shows the
  last known day, with a visible "last updated HH:MM" marker.
- Status updates write to IndexedDB first, then attempt the network. The UI
  reflects the local write immediately.
- A background sync drains the queue in order when connectivity returns.
- Writes go through a serial queue — no concurrent IndexedDB transactions
  racing each other.
- The queue survives app close and device restart.
- Show pending count somewhere unobtrusive when the queue is non-empty.

## Definition of done

- Installs to a phone home screen, opens without a browser chrome
- A technician logs in and sees only their own jobs for today
- Tapping through en route -> arrived -> complete updates the server
- Airplane mode: all three transitions still work, UI updates, queue holds
- Reconnecting drains the queue with no duplicates, verified by event count
- A dispatcher reassignment surfaces on the technician's phone as a change notice
- Completing a job early or late is reflected in a subsequent re-optimisation
- Lighthouse PWA audit passes

## Build order

One phase at a time. Stop after each.

1. `mobile/` scaffold, Docker service, manifest, service worker, installable
   shell that renders a hardcoded day
2. Auth: token issuance, `/field` scoping, login flow
3. `GET /field/today` and the Today screen against real data
4. Job detail screen, navigate handoff, call handoff
5. Status events: schema, endpoint, the three-state action button, online only
6. IndexedDB queue and offline sync, with the serial write queue
7. Complete screen and `job_completions`
8. Schedule changes: detection on the dispatcher side, `/field/changes`, the
   interrupt screen
9. Feed status events into re-optimisation — actual completion times replace
   predicted ones
10. Lighthouse pass, offline test matrix, device testing on a real phone
    - [x] **Authenticate the dispatcher endpoints before any device test.**
          `/technicians/{id}/access-code`, `/technicians/{id}/access` and
          `/technicians/access` currently have no auth, like the rest of the
          console. Anyone who can reach the API can mint a token for any
          technician and read that technician's day. That is tolerable only
          while the API is bound to localhost -- and this is the phase that
          puts a LAN address into `CORS_ORIGINS` and stops it being
          theoretical. Close it here, not after.

          DONE: `DISPATCH_TOKEN`, a shared secret on every dispatcher-side
          router. Empty is allowed only while `CORS_ORIGINS` trusts nothing
          but localhost -- trust a non-local origin without one and the API
          answers 503 rather than serve an open console to a network.

## Notes to self

Phase 6 is the one that matters and the one that will take longest. Everything
else is screens.

Test offline properly — airplane mode on a real device, not devtools throttling.
The failure modes only show up when the radio is actually off.

Status tracking is also surveillance. The apps technicians adopt are the ones
where they get something back: better routes, less waiting, no chasing dispatch.
The ones they resent are pure monitoring. Keep the balance visible in what gets
built — this app should feel like it works for them.
