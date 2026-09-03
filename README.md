# Waypoint

Field service scheduling optimiser. Given a day's jobs and a team of
technicians, produce an assignment and route for each that respects skills,
time windows, working hours, van stock and real road travel times — then
re-optimise around disruption without throwing away work already done.

It is a Vehicle Routing Problem with Time Windows plus skill matching, solved
with OR-Tools CP-SAT, checked by an independently-written verifier, measured
against two baselines, and driven from a map UI.

See [DISPATCH-SPEC.md](DISPATCH-SPEC.md) for the original specification.

---

## Contents

- [Setup](#setup) — from nothing to a solved day on a map
- [Architecture](#architecture) — what each piece does and why it exists
- [Technician app](#technician-app) — the field PWA, and how a technician signs in
- [Offline](#offline) — the outbox, the serial queue, and what fails permanently
- [Completing a job](#completing-a-job) — parts, notes, photos, and where they live
- [When the day changes](#when-the-day-changes) — detection, the interrupt, and what earns one
- [Re-optimising around reality](#re-optimising-around-reality) — actual times, and why facts break models
- [Benchmark](#benchmark) — the numbers, and which one survives a challenge
- [Reference](#reference) — seeding, migrations, tests, conventions
- [Troubleshooting](#troubleshooting) — what bites on a fresh clone
- [Status](#status) — what is verified, what is limited

---

## Setup

### Prerequisites

| | |
|---|---|
| Docker Desktop | with a WSL2 backend on Windows |
| Docker memory | **10 GB** in `.wslconfig`. Verify with `docker info --format '{{.MemTotal}}'` — this stack peaks around 3 GB and the OSRM build needs headroom |
| Disk | ~2 GB for the OSM extract and routing graph |
| DeepSeek API key | optional; only natural-language dispatch needs it |

If you run other stacks on the same daemon (n8n, Ollama, Qdrant), they share
that memory budget. `api` and `worker` are capped at `mem_limit: 3g` so a
runaway solve cannot take the daemon down with it — see
[Memory](#memory-use-4-workers-not-8) for the incident that motivated it.

### 1. Bring up the stack

```bash
cp -n .env.example .env               # -n so an existing .env is not clobbered
docker compose up -d --build          # db, redis, api, worker, web
docker compose exec api alembic upgrade head
```

`/health/db` returns **503 until the migration is applied** — that is the
schema check doing its job, not a broken container.

| service | port | what |
|---|---|---|
| web | 3000 | map, timeline, drag-to-reassign, dispatch bar |
| api | 8000 | REST + OpenAPI docs at `/docs` |
| db | 5432 | Postgres 16 + PostGIS 3.4 |
| redis | 6379 | Celery broker |
| osrm | 5000 | travel-time matrix (profile-gated, see below) |

### 2. Build the routing graph

Once, ~10 minutes, mostly the download. OSRM sits behind a compose **profile**
so it does not start with everything else — the graph has to exist first.

```bash
./scripts/build_osrm.sh
docker compose --profile osrm up -d osrm
docker compose exec api python -m routing.verify
```

The build crops Malaysia's OSM extract to a Klang Valley bounding box
(239 MB → 51 MB → a 673 MB graph) before running the MLD pipeline. Without the
crop the graph is several GB.

### 3. Seed a day

**After OSRM, not before.** Seeding snaps every generated coordinate onto the
road network, which needs OSRM running. Seed without it and the day is still
usable — nothing fails — but the coordinates stay exactly where the generator
put them, some of them in parks and water, and you get a warning saying so.

```bash
docker compose exec api python -m data.seed --truncate --day 2026-09-03
```

Expect `Road snapping (http://osrm:5000): snapped 29, already on a road 22,
max 386m, mean 61m`.

### 4. Solve

Open <http://localhost:3000> and press **Solve**.

### 5. Optional — natural-language dispatch

```bash
# .env
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-...        # platform.deepseek.com -> API keys
DEEPSEEK_MODEL=deepseek-chat
```

```bash
docker compose up -d api worker      # env vars are read at container start
curl localhost:8000/dispatch/provider
```

No key? `LLM_PROVIDER=ollama` with a local model works with no credential.

### Health

```bash
curl localhost:8000/health           # liveness
curl localhost:8000/health/db        # postgis + applied migration
curl localhost:8000/health/routing   # which travel provider is live
curl localhost:8000/health/config
```

`/health/routing` matters more than it looks. With `ROUTING_PROVIDER=auto`, a
stopped OSRM container silently degrades to haversine and every duration
becomes wrong by roughly a third while everything keeps working. This makes
that visible, and any matrix that did not come from OSRM is marked
not-reportable so it cannot leak into a benchmark figure.

Solves triggered by a request (drag-to-reassign, dispatch apply) use
`SOLVER_WORKERS`, default **4**.

---

## Architecture

```
api/          FastAPI: routes, schemas, db access, the solve service
routing/      travel-time providers behind one interface, with a pair cache
solver/       problem -> CP-SAT model -> schedule -> independent checker
dispatch/     natural language -> typed change -> validated -> re-optimised
worker/       Celery tasks
bench/        baselines and the comparison harness
data/seed/    deterministic Klang Valley day generator
web/          Next.js 15 + React 19 + MapLibre
mobile/       Next.js 16 + React 19, technician PWA (offline-first)
```

The shape of the whole thing:

```
   seed / db ──► Problem ──► CP-SAT model ──► Schedule ──► checker ──► API ──► map
                    ▲                            │
                    │                            ▼
             travel matrix                 re-optimise (pins)
             (OSRM, cached)                      ▲
                                                 │
                                  natural language ──► DispatchChange
```

### Travel times

`routing/` puts every provider behind one `TravelTimeProvider` protocol, so the
solver never knows whether a duration came from OSRM or a haversine estimate.
Two things make this safe rather than merely tidy:

- **Matrices carry their provenance.** `TravelMatrix.source` and
  `.is_reportable` — only `"osrm"` is reportable, and the benchmark refuses to
  print a headline without it.
- **The cache is per-pair, not per-matrix.** Re-optimisation asks for subsets
  of the day; a whole-matrix cache would miss every time. Measured 45.7 ms cold
  → 0.2 ms cached.

`Coord` is a NamedTuple with **lat first**, the opposite of PostGIS's
`POINT(lon lat)`. Both orderings are written down exactly once, because getting
either backwards does not raise — it silently puts every job in Kenya.

### Solver

```bash
docker compose exec api python -m solver.tiny          # print the tiny problem
docker compose exec api python -m solver.run --jobs 40 --technicians 8
```

One `AddCircuit` per technician over that technician's own node set: local node
0 is their home, 1..K are the jobs they could possibly do. A job enters that set
only if they have the skills and parts and could physically reach it inside its
window on an otherwise empty day — every pair excluded there is a variable that
never exists.

`AddCircuit` rather than assignment booleans plus `AddNoOverlap`, because
NoOverlap stops a technician doing two jobs at once but knows nothing about
travel: it will happily end a job in Klang at 10:00 and start one in Ampang at
10:00. Circuit arcs are what travel time attaches to, and subtour elimination
comes free.

Routes are **open** — start at home, end at the last job. `AddCircuit` produces
a closed tour, reconciled by letting the arc back to the depot exist
structurally while contributing zero to both the time chain and the cost.

[solver/CPSAT-NOTES.md](solver/CPSAT-NOTES.md) is the API reference, including
the hint trap that cost the most here: `AddCircuit`'s real variables are the
arc literals, and a hint that sets only `visit` and `start` is close to no hint
at all.

**Two spec contradictions were resolved in the model**, both documented at the
top of [solver/model.py](solver/model.py):

1. The spec lists "cannot exceed their shift" as hard *and* "overtime" as soft.
   Resolved as a hard cap at `shift_end + allowed_overtime_s`, with every
   second past `shift_end` penalised. Defaults to 0, making the shift purely
   hard.
2. The spec's priority order puts travel above unassigned jobs, which would let
   the solver drop a job to save ten minutes of driving — contradicting its own
   "better to leave one unassigned than produce nothing". Resolved by making
   the unassigned weight strictly dominate any achievable travel total.

**A solve that times out returns the greedy warm start rather than an empty
day.** At 80 jobs / 15 technicians a 5-second limit is not enough for CP-SAT to
find its first feasible solution, and returning nothing would tell a dispatcher
that none of their work can be done. Those results are marked `fell_back`,
shown as **"not solved"** in the UI and `+` in the benchmark.

### The checker

```bash
docker compose exec api python -m tests.fixtures.tiny_schedules
```

[solver/check.py](solver/check.py) validates a schedule against a problem
without importing the model or reusing any of its helpers. It runs on **every**
solve, not just in tests — a solver will happily return a schedule that
violates a constraint you modelled wrong, and it will look plausible on a map.

It is itself tested against four hand-written schedules (one valid, three
subtly broken by 2 minutes, 4 minutes, and an eligibility mismatch) whose
correctness was established by hand against a frozen travel matrix.

### Re-optimisation

Completed and in-progress jobs are pinned — technician *and* time. They stay in
the model rather than being removed, because that is what tells the solver
where each technician physically is and when they become free. Nothing unpinned
may be scheduled in the past. Moving a job already promised to a customer costs
`w_churn` (default 900: only move it if that saves more than 15 minutes of
driving).

A technician who calls in sick keeps the work they already did. Only their
future capacity disappears.

**The preview names the customers to phone, not just the count.** One drag
typically retimes a dozen jobs; almost all stay with the same technician inside
the window that was promised, and nobody rings about those. Only a reassignment
or a drop is a call.

### Natural-language dispatch

Three steps, deliberately separate endpoints so preview-before-commit is
structural rather than a convention:

```
POST /dispatch/parse    text -> one typed DispatchChange. Touches nothing.
POST /dispatch/apply    change -> validated -> re-optimised -> diff. commit=false.
POST /dispatch/apply    commit=true writes a new run.
```

The LLM only ever produces a `DispatchChange` against a closed schema. It never
sees a schedule and never touches the solver. Unparseable input is rejected,
not guessed at.

**The entire LLM surface is [dispatch/parse.py](dispatch/parse.py).** Swapping
provider means adding a `_call_<name>` coroutine that returns raw text, plus a
branch in `parse()`. Everything downstream — JSON extraction, schema
validation, technician resolution, preview, commit — is provider-agnostic.

```bash
LLM_PROVIDER=deepseek  DEEPSEEK_API_KEY=...  DEEPSEEK_MODEL=deepseek-chat
LLM_PROVIDER=ollama    OLLAMA_MODEL=llama3.2              # local, no credential
```

DeepSeek's API is OpenAI-compatible, so it goes through the `openai` client
with `base_url="https://api.deepseek.com"`, `temperature=0` and
`response_format={"type": "json_object"}`. JSON mode removes the whole class of
failures where a model wraps its answer in prose; it does not guarantee the
*right* object, so schema validation still runs.

To measure a backend:

```bash
docker compose exec api python -m dispatch.evaluate --provider deepseek
docker compose exec api python -m dispatch.evaluate --provider ollama --preview
```

[dispatch/evaluate.py](dispatch/evaluate.py) runs seven fixed dispatcher
phrasings covering every supported change kind, plus one that must be refused.
It is not a pytest test on purpose: it costs money, needs a credential, and its
result is a measurement rather than a pass/fail.

| model | score | over |
|---|---|---|
| `deepseek-chat` | **7/7** | 6 of 7 runs; one run scored 6/7 |
| `llama3.2` (3B, local) | 5/7 | stable across 3 runs |

DeepSeek's single miss invented a `before` field for a sentence that stated no
deadline; the schema rejected it and nothing was applied. llama3.2's two misses
both omit a field the schema states plainly (`new_shift_end` for "leave at
4pm", `job_ref` for "job 12") — a 3B capability limit, not a prompt gap.

Every failure across both models was caught by schema validation and refused
rather than applied, which is the containment the design is for.

The fixture that resolves a customer name to a job **fills itself from the day
that is loaded** rather than naming a customer. A hardcoded name goes stale the
moment anyone reseeds, and a stale name turns a model's *correct* refusal into
an apparent miss — the worst failure mode an eval can have. That is not
hypothetical: it happened here, and cost llama3.2 a point it had earned.

---

## Technician app

The dispatcher console is half the system. `mobile/` is the other half: a
technician-facing PWA that shows one person their own day and sends back what
actually happened. See [FIELD-SPEC.md](FIELD-SPEC.md).

```
web/      dispatcher console   localhost:3000
mobile/   technician PWA       localhost:3002
```

Two separate Next apps in two containers. `mobile/` imports nothing from
`web/` — they share the API and the database, and nothing else.

### Signing a technician in

There is no password and no login form. A dispatcher issues a short code and
reads it to the technician, who types it into the app once.

1. Open **<http://localhost:3000/access>** (linked from the console topbar).
2. Press **Issue code** on the technician's row. A code like `WCHE-G2SC`
   appears. It is shown **once** — the database stores only a hash, so a lost
   code is reissued, never looked up.
3. The technician opens <http://localhost:3002>, types the code, and is signed
   in until revoked.

The code is single-use and expires after `ACCESS_CODE_TTL_HOURS` (24 by
default). It buys a 256-bit bearer token, which is what the phone stores and
sends on every `/field/*` request. Splitting the two is the point: a code
short enough to say down a phone line is short enough to guess, and a
credential the phone keeps forever should be neither.

**Revoke** cancels the unused code *and* logs out every signed-in handset. If
it only did the first, a lost phone would keep working.

Codes use [Crockford's Base32](https://www.crockford.com/base32.html) — no
`I`, `L`, `O` or `U`. The API normalises case, dashes, spaces and the
confusable characters on the way in, so a technician who types `o` where the
screen showed `0` still gets in.

### The day

`GET /field/today` returns the authenticated technician's jobs for a date, in
visit order, read from the **latest succeeded** solve run for that day. A
queued or failed run does not replace a working schedule.

```bash
curl localhost:8000/field/today -H "Authorization: Bearer $TOKEN"
curl "localhost:8000/field/today?day=2026-09-03" -H "Authorization: Bearer $TOKEN"
```

Three things about the response are deliberate:

- **It is complete.** Every screen — including job detail — renders from this
  one payload. Phase 6 caches it, and a technician in a basement must still be
  able to open a job and see where they are going, so there is no
  `GET /field/jobs/{id}` that would only work with signal.
- **Times carry the `+08:00` offset**, not `Z`. The PWA reads the hour
  straight out of the ISO string rather than through the device clock, so a
  handset left on the wrong timezone still shows Malaysian job times. Serving
  UTC would put every job on the Today screen eight hours early and look
  entirely plausible.
- **No solved schedule is an empty day, not a 404.** A morning before dispatch
  has run the solve is not a broken app.

One window is returned, not two. The database keeps a hard SLA window and a
softer promised window; the technician was never told about the SLA, and
showing two on a phone invites "which one is real". `window_is_promise` says
which one it is.

`jobs.status` and the phone's statuses are different vocabularies —
`pending`/`assigned` both read as `upcoming`. **`en_route` has no source
yet**: nothing in `jobs.status` means "driving there", and it only becomes
expressible when `job_status_events` lands in phase 5.

### Job detail columns

Migration `0004` adds six nullable columns to `jobs` — `area`, `address`,
`phone`, `service_type`, `fault_description`, `notes`. None of it is solver
input; CP-SAT never reads a street name.

Nothing is backfilled. Rows predating the migration genuinely have no address,
and inventing one would be worse than showing nothing — a technician shown a
fabricated address and sent to it has been actively misled. **Re-seed to get a
day with detail on it:**

```bash
docker compose exec api python -m data.seed --jobs 40 --technicians 8 --truncate
```

They are readable through `GET /jobs` but **not writable** through
`POST`/`PATCH /jobs`: `JobIn` is unchanged, so an existing PATCH cannot null
them by omission. Dispatcher-side editing of a job note has no UI and no
route yet.

### Screens, and the two handoffs

Four screens, no more. Today (`/`) and job detail (`/job?id=…`) are built;
Complete and Schedule changed are phases 7 and 8.

Job detail is `/job?id=4412`, a **query parameter rather than a path
segment**, and that is a trade made for offline. A `/job/[id]` route cannot be
prerendered without knowing every job id at build time, so a technician
deep-linking to it with no signal would get the service worker's fallback
shell instead of the job. `/job` is one static document that serves every id —
the id arrives in the query string, the data comes from the cached day — so
the service worker precaches it once. (Its cache lookup uses `ignoreSearch`,
or `/job?id=4412` would miss and drop the technician back on Today.)

**Navigation hands off, it does not route.** The spec says geo URI, and on
Android that is exactly right: `geo:` opens the system chooser, so Waze and
Google Maps both offer themselves and the technician picks what they actually
drive with — which in Malaysia means Waze more often than not.

`geo:` does nothing on iOS, though. Safari does not register the scheme, so a
bare geo link is a *dead* button on an iPhone rather than a degraded one —
worth calling out because it fails silently. `lib/handoff.ts` detects Apple
devices and hands off to Apple Maps instead. It is not a chooser, but a
working handoff to the wrong preferred app beats a button that does nothing.
(The iPad check reads `navigator.maxTouchPoints`: iPadOS has reported itself
as a Mac since version 13.)

**Calling** is a `tel:` link built from the stored E.164 number. The
phone icon is hidden entirely when a job has no number, rather than shown
inert.

The three-state action button (**On my way → Arrived → Complete**) renders in
its correct state but is **disabled until phase 5**, which owns the status
endpoint. Disabled rather than silently inert: a technician who taps and gets
no response taps again, and phase 5 is where that becomes a duplicate event.

### Status events

`POST /field/jobs/{id}/status` records what a technician reported.

```bash
curl -X POST localhost:8000/field/jobs/19/status   -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json'   -d '{"id":"<uuid>","status":"en_route","at":"2026-09-03T08:03:00+08:00","device_seq":7}'
```

**Two tables, one owner.** The spec said derive status from events and do not
store it in a mutable column. Append-only: yes. The rest could not be taken
literally — `api/repo.py` filters the solver's input on `jobs.status`, so a
field app that only wrote events would leave a completed job `assigned`
forever and every later solve would re-schedule finished work. So:

- **`job_status_events` is the truth.** Append-only, never updated.
- **`jobs.status` is a derived cache**, written in the same transaction.

The out-of-order protection survives intact, because the derivation takes the
**highest-ranked** event rather than the newest row (`en_route` < `arrived` <
`complete`). A queued `en_route` syncing after a `complete` cannot demote the
job. That rule is enforced twice on purpose: in Python for the technician's
view, and in SQL for the cache — the `UPDATE` compares the current rank
against the new one, so `jobs.status` physically cannot move backwards even
under concurrent writes.

**`en_route` has no representation in `jobs.status`, deliberately.** Nothing
in that vocabulary means "driving there", and `in_progress` would be wrong:
`solver/reoptimise.py` pins in_progress jobs as physically under way, and a
technician still in the van is not. The column is left alone and the job stays
movable, which is true. That an en_route job is *more expensive* to move is
real, and is a churn-weight question for phase 9 rather than a status one.

**Idempotency.** The `id` is a UUID generated on the phone before the request
leaves — breaking the repo's integer-primary-key convention, and it should:
nobody reads an event id aloud, and the client picking the key is what makes a
retry safe. The insert is `ON CONFLICT DO NOTHING`, so a replay is a success
that writes nothing. The response carries `duplicate` so the offline queue can
tell a first delivery from a replay. Always `201`, never 200-vs-201: the
status code answers "did this achieve what it asked for", and a replay
achieved exactly what the original did.

**Clocks are not trusted.** `at` is the phone's time — that is the whole point
of working offline — but phones are wrong, sometimes by hours and occasionally
on purpose once someone realises the app records when they did things. The
server stamps `recorded_at` itself and clamps `occurred_at` into a believable
band: never after receipt, never more than 24h before it. The raw value is
kept in `client_occurred_at`, so `occurred_at != client_occurred_at` is how
phase 9 knows a timestamp should not move the schedule. `at` must carry an
offset; a naive datetime is rejected at the schema rather than guessed at.

**`device_seq`** is a per-device counter that only goes up. Two events queued
in the same second — or across a clock change, which is the case that bites —
have no order from their timestamps alone.

### The undo window

Tapping the action button does **not** send immediately. It applies locally,
shows `Undo · 5`, and sends when the window closes.

This is an addition to the spec, and it exists because the two constraints
collide: the button is pressed one-handed with gloves, and the event log is
append-only, so a mis-tap that reaches the server is permanent. Undoing it
afterwards would need a compensating event — two conflicting records of the
same moment. Putting the correction *before* the write gives a boundary that
fits in a sentence: within five seconds nothing happened, after five seconds
it is on the server.

The pending write lives in a module (`lib/status-writer.ts`), not in component
state. A technician who taps "Arrived" and immediately hits back would
otherwise lose the event on unmount — strictly worse than having no undo at
all. Leaving the screen **flushes** it. `at` is stamped at the tap, not at the
send, so the recorded time is when they said it happened.

## Offline

The part that decides whether this is usable. A technician in a basement, in a
lift shaft, or in the two kilometres of Jalan Kuching with no signal must be
able to do their job and have it count.

### Two stores, chosen for opposite reasons

| | Where | Why |
|---|---|---|
| **The outbox** | IndexedDB | Holds work that cannot be regenerated. Durable, transactional, and able to hold blobs when photos land in phase 7. |
| **The cached day** | localStorage | Holds something the server will happily resend. Chosen for being **synchronous** — it is read inside the first render and painted with no await. |

That second row is the whole reason the app opens instantly with the radio
off. IndexedDB cannot do it: every read is a promise, so the first paint would
be a skeleton no matter how fast the disk is. One `await` is enough to break
"it opens, it shows the next job".

The load order is: cached day painted synchronously → outbox merged over it →
network asked in the background. A failed refresh is no longer an error
screen, it is a line saying how old what you are looking at is.

### The write path

Tapping the action button writes to IndexedDB and **that is what "saved"
means**. The network is a detail that happens afterwards, possibly hours
later. `lib/sync.ts` drains on the `online` event, on the app coming to the
foreground, and on a 5s→60s backoff while anything is waiting.

Every write goes through **one promise chain** (`serial()` in `lib/db.ts`).
IndexedDB transactions are individually atomic, which is not the same as safe:
the races here span several transactions — a drain reading an item, deciding
it succeeded and deleting it while an enqueue appends, or two drains triggered
by an `online` event and a visibility change landing together and both sending
the same item. That second one is what "no duplicates, verified by event
count" is about, and per-transaction atomicity does not prevent it.

Two details in `serial()` that are easy to get wrong and are commented at the
source: `tail.then(op, op)` passes the operation as **both** handlers so one
failed write does not wedge the queue forever, and the stored tail is the
*swallowed* promise so the chain does not accumulate unhandled rejections.

### What happens when a send fails

The distinction that matters is **can this ever succeed**.

- **No reply, 5xx, 408, 429** → retry. The drain stops at the first one and
  keeps everything after it, because the queue is ordered and phase 7's
  completion has to land after its own `complete` event.
- **Other 4xx** → permanent. Marked and stepped over, so one dead item cannot
  hold every later one hostage. In practice this is a **404: the job was
  reassigned while the phone was offline.** The item is *marked, not deleted* —
  quietly dropping it would mean a technician's completed job vanished with
  nothing to show it was ever reported. Phase 8 turns the count into a
  sentence naming the job.
- **401** → the token is dead, which is not the item's fault. The drain stops
  and the item is left untouched; it goes out after signing in again.

### Sign-out is refused while anything is unsent

Signing out clears the token, and the queue cannot send without one. The
button is disabled with the reason underneath. This is the difference between
"log out" and "throw away the last hour of work".

### Not the Background Sync API

`registration.sync` would let the service worker drain with the app fully
closed. It is Chromium-only — no Safari, no Firefox — and would need a second
copy of the queue logic and the bearer token inside the worker. For an app
installed to a home screen and left open across a shift, the moments that
matter are the radio returning and the technician looking at the screen, both
of which the page sees. **This is a real divergence from the spec's wording.**

**Phase 10 verdict: still no, pending one device test.** What the page-driven
syncer already covers, verified in the matrix: radio returning, app
foregrounded, a 5s→60s backoff while anything waits, and an immediate attempt
after every write. What it cannot cover is a phone whose screen has been off
long enough for the browser to freeze or discard the page — and whether that
happens in practice depends on the handset, the Android battery policy and how
long the app sits idle, none of which an emulator answers.

**The check that decides it**, on a real device: queue some work with the radio
off, restore signal, then lock the phone and leave it five to ten minutes
without opening the app. Then look at the server.

- **Queue drained** → the page stayed alive; nothing to do.
- **Queue still waiting, and drains the moment you unlock** → this is the
  status quo working as designed. Whether it is *good enough* is a judgement:
  a technician who reports a job and pockets the phone for an hour has a
  delayed report, not a lost one, and phase 9 uses `occurred_at` rather than
  arrival time — so the schedule is still re-planned around the right moment
  when it does land.
- **Queue still waiting after unlocking too** → something is wrong with the
  syncer, not with the absence of Background Sync.

Only the second outcome makes Background Sync worth its cost, and only if the
delay is long enough to matter to dispatch. If it is, the cheaper fix is
probably a `periodicSync` or a plain `setInterval` kept alive by an audio-free
wake lock — not moving the queue into the worker.

### Testing it

`mobile/` now has **vitest** and **fake-indexeddb** (two new devDependencies).
fake-indexeddb runs the actual W3C algorithms — transaction lifetimes,
auto-commit, key ordering — so the queue is tested against real IndexedDB
semantics rather than a stub of our own design.

```bash
cd mobile && npm test          # 37 tests: outbox, drain, cache
```

The airplane-mode path needs a **production build**, because offline
navigation is the service worker's job and the worker is off in dev:

```bash
cd mobile && npm run build && npm start
```

## Completing a job

Screen 3, and the spec's reasoning is the design constraint: *if this takes
too long people stop filling it in and the data becomes worthless.* Three
things and a button — parts, notes, an optional photo.

**Parts are prefilled with what was planned.** The technician's job on this
screen is to correct the exceptions, not to re-enter what the schedule already
knew. Anything else is behind one tap ("+ Used something else"), because eight
checkboxes on every completion is exactly the friction that stops people
filling it in.

**The finish time is stamped when the screen opens**, not when Done is tapped.
The technician finished the job and *then* filled in a form; the minute of
typing is not part of the job, and phase 9 re-plans the afternoon off that
number.

### Two queue items, one order

Finishing a job is two facts: the `complete` **status event** (when it
finished) and the **completion** (what it needed). `POST .../complete`
deliberately does *not* fabricate a status event — that would put two records
of the same moment in an append-only log — so the event has to land first.
`lib/completion.ts` enqueues them in that order and the FIFO outbox keeps them
that way, whether the drain happens two seconds later or after forty minutes
in a plant room.

### One completion per job

`job_id` is the **primary key** of `job_completions`, not a surrogate. That is
the real constraint — a job is finished once — and making it the key means a
retry from the offline queue is `ON CONFLICT DO NOTHING` by construction
rather than something the client deduplicates for itself.

`GET /field/today` reports `completed` per job so the Complete screen can
refuse a second submission rather than accepting a form the server will
silently discard. It is deliberately **not** the same as `status ==
"complete"`: the status comes from the event log and the completion is a
separate record, and between the two arriving there is a real window —
milliseconds online, a whole dead zone offline — where a job is done and its
paperwork is not.

### Photos

**No object storage, and no new Python dependency.** Three decisions:

- **Where the bytes live: a directory on a volume** (`PHOTO_DIR`), behind the
  four functions in `api/photos.py`. Postgres was easy to rule out — a few
  hundred KB per job is nothing to serve and a great deal to carry in every
  backup, for data no query looks at. Adding MinIO plus a client library would
  have been the *larger* decision, not the smaller one. `photo_key` is a key
  into a store; swapping the store for S3 rewrites one file.
- **How they travel: base64 in JSON**, not multipart. Multipart is the
  idiomatic answer and needs `python-multipart`, which this project does not
  have. Base64 costs 33% more bytes and needs nothing new — and it keeps every
  outbox payload plain JSON, so the queue built in phase 6 handles photos with
  **no changes at all**. That trade reverses at full resolution.
- **The client downscales first**, to 1600px on the longest edge at quality
  0.72. Verified: a 4000×3000 handset photo arrives as 1600×1200. This is not
  a nicety — a raw 3-8MB photo is an upload that will not finish over a weak
  connection from a basement, which is precisely where these get taken.

`createImageBitmap(file, { imageOrientation: "from-image" })` is the part
that is easy to miss: phone cameras write the sensor's raw orientation plus an
EXIF tag, and a canvas draw ignores the tag — so without it every portrait
photo reaches the dispatcher on its side.

Photos are scoped through the completion row, not inferred from the filename.
A UUID is unguessable; it is not a permission. Keys are validated as
`<uuid>.jpg` before touching the filesystem, because the key arrives in a URL
path.

## When the day changes

`/field/today` reads the latest succeeded solve run. That means a
re-optimisation at 11:40 **rewrites what a technician is looking at**,
silently — and without a record of the delta, the only way they find out is by
noticing the screen changed. Which is the phone call this app exists to
replace.

### Detection

`api/schedule_changes.py` compares a new run against the one it supersedes and
records four kinds: `removed`, `cancelled`, `assigned`, `retimed`.

It hooks into **`repo.store_result`**, which is the only place assignments are
written — a plain solve, a drag-to-reassign, a typed dispatch instruction and
the Celery re-optimisation all pass through it. Wired anywhere else it would
have covered some paths and quietly missed the rest.

The first schedule of a day produces nothing: nobody had seen anything to be
changed from.

**Not `solver/reoptimise.py`'s `diff()`,** which computes the same delta and
which I expected to reuse. It takes two solver `Schedule` objects, and
building one needs a `Problem`, which needs a travel matrix — so reusing it
would put OSRM in the path that fires a notification, and let a failed routing
provider stop a technician being told their job moved. The comparison here is
two dictionaries out of the assignments table. Same logic, different input,
different audience: `diff()` shows a dispatcher a preview, this tells a
technician about something already committed.

### The interrupt, and what earns one

```
GET  /field/changes          unacknowledged, oldest first
POST /field/changes/{id}/ack idempotent, 204
```

The takeover fires only for the spec's trigger — *reassigns or cancels* — plus
newly-added jobs. **A retime never takes the screen on its own.**

That threshold is deliberate and different from the solver's.
`solver/reoptimise.py` treats anything over 60 seconds as a real move, which
is right for a dispatcher reviewing a delta. `SCHEDULE_CHANGE_RETIME_MINUTES`
defaults to **15**, because a re-solve nudges half a dozen jobs by twenty
minutes most times it runs, and a full-screen takeover on each would teach
people to dismiss without reading — costing exactly the one that mattered.

Ordering matters as much as filtering. Sorted by arrival, the sentence that
changes where somebody drives lands fifth on the screen, under four retimes.
Disruptive cards come first; every retime collapses into one summary card.

**One button for all of it.** "Got it" is an acknowledgement that the
situation has been taken in, and making somebody tap four times to say that
once is how a notice becomes an obstacle.

### Acknowledgement is queued

Acks go through the same outbox as everything else (`kind: "ack"`). Tapping
"Got it" in a basement dismisses the interrupt immediately and durably —
without this it would reappear on the next refresh. Verified: acknowledged
offline, drained on reconnect, stayed gone.

### The other side of a reassignment

A technician who marked a job done in a dead zone, and whose job was
reassigned before the phone found signal, gets a **404** on that queued write.
Phase 6 recorded it and showed a count; this screen is the sentence — *"This
job isn't yours any more, so what you filled in couldn't be saved. Tell
dispatch if you did the work."*

Those items move to a `seen` state once explained: **marked, never deleted**.
The record that somebody reported doing the work is exactly what may need
reconstructing later, but leaving it in the count would mean the warning strip
never cleared and stopped meaning anything.

## Re-optimising around reality

The reason the field app exists. Before this, re-planning at 11:40 assumed
every job finished exactly when the morning's solve predicted — so a
technician running forty minutes behind was re-planned as though they were on
time, and the new schedule was wrong from the moment it was produced.

```bash
curl -X POST localhost:8000/solve/reoptimise   -H 'content-type: application/json'   -d '{"run_id":1,"now":"09:33","commit":false}'
```

Previews by default. `commit=true` stores the result as a new run, which makes
it the schedule `/field/today` serves and fires the phase 8 change notices —
so a re-plan reaches the technicians it affects the same way a dispatcher's
reassignment does.

What a report changes:

- **A reported state beats an inferred one.** "It was due to finish an hour
  ago, so it is done" is a guess, and it is the guess this replaces.
- **Reported times replace predicted ones**, so a job that really ran until
  10:50 anchors that technician as busy until 10:50.
- **A measured duration replaces the estimate.** A finished job's length stops
  being a guess, which also stops the same optimistic figure being used for
  the same customer every visit.
- **`en_route` pins the technician and not the clock.** They are in the van
  and committed, so giving the job to somebody else means two people driving
  to one address — but nobody knows when it starts. `Pin.start_s=None` already
  expressed exactly that, which closed the gap phase 5 left open.
- **Drift is reported as a number.** `{"T1": 50}` — fifty minutes behind. It
  falls out of the pinning either way, but a figure a dispatcher can read
  beats a consequence they have to infer from a redrawn timeline.

### Facts do not have to satisfy the model

This is the hard-won part, and it cost four separate bugs that all failed
identically: **an unsatisfiable model returns an empty schedule, which reads
exactly like a considered plan in which every job on the day was dropped.**
Silent, plausible, catastrophic.

The model's constraints describe what may be **decided**. A report describes
what **happened**, and the past is under no obligation to satisfy them — or to
be consistent with itself:

| What reality did | What the model said |
|---|---|
| A job overran its own SLA window | Hard windows are inviolable |
| A job left its technician on site when the schedule had them elsewhere | Two pinned jobs cannot overlap on one person |
| An arrival 59 seconds before the schedule's own start | Nobody arrives before the travel matrix allows |
| A technician catching up on paperwork tapped four jobs at 14:51 | Nobody is at four addresses in one minute |

`vet_actuals()` in `solver/reoptimise.py` is the single guard. A technician's
reported times are used only if they could **all be true together** — each job
starting no earlier than the previous one could have finished, plus the drive
between them. If they cannot, that technician's reports keep `trusted=False`,
which everything downstream already reads as *this happened, but not
necessarily when*. Their jobs still pin, to the right people, in the right
order; only the timings fall back to the schedule.

**One trust decision, taken once.** Pins, measured durations, widened windows
and the drift figure all read the same flag. Deciding it per consumer meant
four places that could disagree, and two of them disagreeing is what made the
model unsatisfiable the first time. It widens the same flag the server's clock
clamp sets (`api/field_status.py`), rather than inventing a second notion of
doubt.

**Widening is only ever for reported work.** A hard window constrains
decisions, and a finished job has no decision left — so its window opens to
admit what happened. But a job pinned by *inference* is a guess, and widening
on a guess lets the solver park work outside its SLA and call it legal. A
committed schedule then stores that as fact and the next re-optimisation
widens from there: the violation compounds one run at a time until the
independent checker rejects a day nobody did anything wrong on. That happened
too, and there is a regression test for it.

**An empty schedule is reported as a failure**, never as a plan. `ok` is false
and nothing is committed — replacing a working day with nothing is not an
improvement, and "38 jobs moved, 38 customer calls" reads like a decision.

### Where the guesswork still lives

A technician who reports nothing still gets the old clock-based inference,
because for them there is no better information. That shrinks as people report
and disappears on a day where everyone does — and it is the last place in the
system where a schedule is built on an assumption about what somebody did.

### Scoping

Every `/field/*` route resolves the technician from the bearer token. There is
no `technician_id` parameter anywhere under `/field`, so a client cannot
express the question "show me someone else's day". Scoping goes in the WHERE
clause rather than an `if` after the fetch, which is also what makes
"404, not 403" fall out for free — the row is simply not in the result set,
and the API never confirms that a job it will not show you exists.

### Audits, and one the spec asked for that no longer exists

```bash
cd mobile && npm run build && npm start
node tests/lighthouse.mjs        # scores + installability
node tests/offline-matrix.mjs    # 17 offline scenarios end to end
```

**The spec asks for "Lighthouse PWA audit passes". That audit was deleted.**
Lighthouse 12 removed the PWA category in 2024; the categories that remain
are performance, accessibility, best-practices and seo. There is nothing to
make pass.

`tests/lighthouse.mjs` therefore does two things: runs the categories that do
exist, and checks the eleven installability criteria Lighthouse used to check,
directly. That is a closer reading of what the spec wanted than a score for an
audit that no longer runs.

| | |
|---|---|
| Performance | **100** |
| Accessibility | **100** |
| Best Practices | **100** |
| SEO | **100** |
| Installability | **11/11** |

Accessibility was 93 until this phase, and the seven points were **my own
mistake from phase 1**. I had set `user-scalable=no`, reasoning that the app
must not zoom when an input takes focus. iOS only does that for inputs under
16px, and every input here is well over it — so the behaviour being prevented
could not happen, and what it actually cost was a technician with low vision
being unable to zoom at all. WCAG 1.4.4. Large type is not a substitute for
letting somebody make it larger.

### The offline matrix

`mobile/tests/offline-matrix.mjs`, run against a production build with the
worker active. Not a vitest suite: it needs a real browser, a real service
worker and a real API, which is three things vitest is deliberately not.

| Scenario | Result |
|---|---|
| Service worker controls the page | pass |
| Day cached for offline use | pass — 7 jobs |
| Today opens offline from cache | pass |
| Staleness shown while offline | pass — `Offline · updated 12:20` |
| Job detail deep-links offline | pass — address intact |
| Navigate handoff works offline | pass — `geo:3.189,101.686?q=…` |
| All three status transitions offline | pass |
| Complete form opens offline | pass |
| Everything queued, nothing lost | pass — 4 waiting |
| Sign out refused while unsent | pass |
| Queue survives an app restart | pass |
| Done group reflects local work | pass |
| Queue drains on reconnect | pass |
| Sign out allowed once sent | pass |
| Server agrees the job is done | pass — `complete`, `completed=true` |
| Cached day dropped on sign out | pass |
| No page errors throughout | pass |

### Testing on a real phone

**Read this first: `http://192.168.x.x` is not a secure context, and that
breaks more than you would expect.**

Browsers gate a set of APIs on a "secure context" — HTTPS, or `localhost`. A
plain LAN address is neither. What that costs here:

| API | Gated? | Consequence on `http://192.168.x.x` |
|---|---|---|
| **Service Worker** | **yes** | **No offline shell. Not installable. Reloading or deep-linking offline gives the browser's error page.** |
| `crypto.randomUUID()` | yes | Already handled — `lib/uuid.ts` falls back to `getRandomValues` |
| `navigator.storage.persist()` | yes | Not used |
| Geolocation | yes | Not used |
| **IndexedDB** | no | The outbox works |
| **localStorage** | no | The cached day works |
| `crypto.getRandomValues()` | no | Which is why the UUID fallback works |

So over a plain LAN address the app still *runs* and status updates still
queue offline — but the thing phase 6 and 10 are about, the installable
offline-capable PWA, is not what you would be testing. **Do not evaluate the
offline behaviour that way and conclude it is broken.**

#### The clean way (Android, USB cable)

`adb reverse` makes the phone treat your machine's ports as its own
`localhost`, which **is** a secure context. It also means no CORS change and
no `NEXT_PUBLIC_API_BASE` change — the phone uses exactly the URLs the desktop
uses.

```bash
adb reverse tcp:3002 tcp:3002      # the PWA
adb reverse tcp:8000 tcp:8000      # the API

cd mobile && npm run build && npm start     # production build: the SW only registers there
```

Then open `http://localhost:3002` **on the phone**. Everything works, including
install-to-home-screen.

#### The other way (any device, including iOS)

A real HTTPS origin. Either a tunnel (`cloudflared tunnel --url
http://localhost:3002`) or a local cert. With a tunnel you must also expose
the API and point the app at it:

```bash
# .env
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,https://<your-tunnel-host>
NEXT_PUBLIC_API_BASE=https://<your-api-tunnel-host>
DISPATCH_TOKEN=<generate one — see below>

docker compose up -d api mobile      # restart alone will not reload env
curl localhost:8000/health/config    # confirm what the process actually loaded
```

#### `DISPATCH_TOKEN` becomes mandatory

The moment `CORS_ORIGINS` contains a non-local origin, the API **refuses** to
serve dispatcher routes without a token — 503, not a warning in a log:

```bash
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

Put it in `.env` as `DISPATCH_TOKEN=...`. The console will ask for it once and
remember it. `/health/config` reports `dispatch_auth` as `on`, `off`, or
`REQUIRED-BUT-UNSET`, and is readable without the token on purpose — "is this
thing open" is the question you most want answerable from a machine that
cannot get in.

#### What to actually check on the device

The emulated matrix passes 17/17 (`node mobile/tests/offline-matrix.mjs`), so
the useful device checks are the ones an emulator cannot answer:

1. **Install to the home screen.** Does it open without browser chrome?
2. **Real airplane mode**, not devtools throttling. Toggle the radio, do a full
   en route → arrived → complete, watch the pending count, turn it back on.
3. **Lock the phone mid-queue**, wait five minutes, unlock. Does the queue
   drain? (This is the Background Sync question — see below.)
4. **Force-quit the app** with items queued, reopen. They should still be there.
5. **Sunlight.** Take it outside. This is the one thing no amount of testing
   indoors settles.
6. **The Navigate button** — does it actually open Waze/Google Maps with the
   right destination?

### Dispatcher access

The console is behind one shared secret, `DISPATCH_TOKEN`, sent as a bearer
token on every dispatcher-side route. It is **not** per-dispatcher and does
not pretend to be: there is one dispatch team sharing one console, and this
answers *"is this the office"*. The technician token answers *"whose day"*.
That asymmetry is deliberate — the technician side needed identity, the
dispatcher side needed a lock.

Applied at the router, not per route, so a handler added later cannot forget
it. `/health*` is never behind it (a locked healthcheck would stop
`compose up` converging) and neither is `/field/*` — a technician's phone
holds its own token and must never be given the dispatch secret.

Empty is allowed **only** while `CORS_ORIGINS` trusts nothing but localhost.
Trust a non-local origin without setting one and every dispatcher route
answers 503 with the reason. Failing closed beats a warning in a log: the
condition that makes the hole real is exactly the condition that now makes the
token mandatory.

```bash
# run the host suite against a locked stack
WAYPOINT_DISPATCH_TOKEN=$DISPATCH_TOKEN .venv/Scripts/python -m pytest tests/test_api.py
```

---

## Benchmark

```bash
docker compose exec api python -m bench.harness \
    --sizes 20x5,40x8,80x15 --seeds 1,7,42 --limits 5,30,120 --workers 4 --reopt
```

### Results

3 sizes × 3 seeds × 3 time limits, OSRM matrix, 4 workers. Every one of the 45
runs passed the independent checker.

```
                      travel     per job    assigned     SLA met
----------------------------------------------------------------
greedy_nn               560m       15.1m        39.8        36.9
cluster_nn              448m       13.1m        35.9        33.4

solver 120s             514m       12.5m        42.6        42.4
solver 30s              560m       13.2m        42.3        42.1
solver 5s               537m       13.5m        40.4        38.8
```

Positive is better in both columns below; on travel per job that means a
reduction.

| vs | jobs done | travel per job |
|---|---|---|
| greedy_nn (best baseline on jobs) | **+7.0%** | +17.1% |
| cluster_nn (best baseline on travel) | +18.6% | **+4.3%** |

Eight of the 20-job runs were **proved optimal**. At 80 jobs the 5-second
column is greedy, not the solver — CP-SAT does not reach its first feasible
solution in that budget, and those rows are marked `+`.

Re-optimisation against a midday disruption, 80 jobs: pinning costs 459 extra
minutes of driving and saves **57 customer phone calls** (5 vs 62).

### Which number I would stand behind

**+7.0% more jobs completed than the strongest baseline, at a 120-second
limit.** If someone challenged the headline, that is the one I would defend,
and I would volunteer the reasoning rather than wait to be asked.

The rule I applied: **beat the best baseline on each metric separately.**
greedy_nn is the strongest baseline on jobs done (39.8), so the jobs claim is
measured against it — +7.0%, not the +18.6% available against cluster_nn.
cluster_nn is the strongest on per-job travel (13.1m), so the travel claim is
+4.3%, not the +17.1% available against greedy_nn. Quoting +18.6% *and* +17.1%
together would mean picking a different opponent per metric, which is how
benchmarks lie.

What actually makes the result solid is neither percentage: **the solver is the
only strategy that beats both baselines on both axes at once.** greedy_nn buys
jobs with driving; cluster_nn buys low driving by doing fewer jobs; the solver
dominates both simultaneously. That is a claim a reader can check by reading
down two columns, and it does not depend on which baseline you consider fair.

**One correction to a natural assumption.** It is often true that a baseline
gets lower per-job travel *by leaving the awkward jobs unassigned* — the ones
far out, or with tight windows — because per-job travel is an average over
whatever it chose to do, and dropping the expensive tail flatters the average.
That effect is real here, but it is **cluster_nn** that shows it, not
greedy_nn: cluster_nn does the fewest jobs of any strategy (35.9) at markedly
lower per-job travel than greedy (13.1m vs 15.1m). Individual rows are starker
— on `20j/5t s42`, cluster_nn posts 10.9m per job while doing 16/20, against
the solver's 14.2m while doing 19/20. On that instance the baseline "wins" on
travel purely by not attempting three jobs.

greedy_nn is simply worse on both metrics — more driving *and* fewer jobs — so
the +17.1% against it is not inflated by the dropped-tail effect. It is
inflated by greedy_nn being a weak opponent, which is the same problem wearing
a different hat. Either way the conclusion holds: jobs-completed against the
strongest baseline is the fair comparison, and +7.0% is the number.

Caveats I would raise before being asked: three seeds is n=9 instances, not a
population; OSRM's car profile is free-flow with no traffic model, so none of
this reflects a KL rush hour; and all figures come from one machine.

### How the baselines are kept honest

Two baselines: pure greedy nearest-neighbour, and cluster-then-NN (k-means
regions, one per technician). Both pick the next job by **drive time plus idle
wait**, not drive time alone — ranking on distance alone sends someone four
minutes down the road to sit outside a locked building for two hours, which no
dispatcher does. The headline compares against whichever baseline scored best,
so the solver never gets credit for beating a strawman.

Travel is reported **per assigned job**. Raw totals are not comparable when
strategies assign different numbers of jobs: a schedule doing 13 of 20 beats
one doing 18 on total travel while being plainly worse.

The harness refuses to print a headline figure unless the matrix came from
OSRM.

### Memory: use 4 workers, not 8

CP-SAT keeps a full copy of its search state per worker. Measured on one 80-job
/ 15-technician solve at a 120s limit:

| workers | assigned | peak RSS |
|---|---|---|
| 1 | 61/80 | 427 MB |
| 2 | 69/80 | 663 MB |
| 4 | 70/80 | 747 MB |
| 8 | 70/80 | 1616 MB |

8 workers buys nothing over 4 and costs more than twice the memory. At 8, a
full benchmark run exhausted Docker's whole allocation and got every container
on the daemon OOM-killed — unrelated stacks included. `api` and `worker` now
carry `mem_limit: 3g`, and request-triggered solves use `SOLVER_WORKERS`
(default 4).

---

## Reference

### Seeding

```bash
docker compose exec api python -m data.seed --dry-run
docker compose exec api python -m data.seed --truncate --jobs 80 --technicians 15 \
    --day 2026-09-03 --seed 7
```

Same `--seed` produces the same day on any machine — the benchmark depends on
it, and it is covered by a cross-process test. `--orphan-jobs N` adds jobs
requiring a skill nobody holds, for exercising the explainer.

Generated coordinates are **snapped to the road network** via OSRM
`/nearest` before anything is written (`--no-snap` opts out). Points are drawn
from a Gaussian around a district centre, so a few land in parks and water;
OSRM would otherwise drag those to the nearest road silently on every single
request, meaning the travel times were measured from somewhere the job is not.
On the default 40-job day: 29 of 51 coordinates moved, mean 61m, max 386m.
Snapping is idempotent, and refuses to move anything more than 2 km — a point
that far from a road is a bug, not an imprecise address, and hiding it would
defeat the warning that exists to catch it.

Jobs are **clustered**, not uniformly scattered. This is a benchmark-honesty
issue: uniform points have no cluster structure, which makes a greedy baseline
look worse than a real dispatcher and inflates the apparent improvement. A test
asserts mean nearest-neighbour distance stays under 1.2 km.

### Migrations

```bash
docker compose exec api alembic upgrade head
docker compose exec api alembic downgrade -1
alembic upgrade head --sql                 # preview SQL, no database needed
```

> ### ⚠️ `downgrade` past `0004` or `0005` destroys data
>
> `0004` added `area`, `address`, `phone`, `service_type`,
> `fault_description` and `notes` to `jobs`. Dropping a column drops its data,
> and nothing else in the schema holds that text — so
> `alembic downgrade 0003` (or `downgrade -1` from head) followed by
> `upgrade head` leaves **six NULL columns, not the values that were there**.
>
> Recovery is a re-seed, which truncates everything:
>
> ```bash
> docker compose exec api python -m data.seed --jobs 40 --technicians 8 --truncate
> ```
>
> **`downgrade` past `0005` is worse.** It drops `job_status_events`, the only
> record of what technicians reported. `jobs.status` keeps whatever the cache
> last held, so the schedule still *looks* right afterwards — which is exactly
> what makes the loss easy to miss. No re-seed brings these back.
>
> **`downgrade` past `0006`** drops what technicians recorded about finished
> work, and leaves the photo files on the volume with nothing referencing them
> — the disk usage stays and the meaning does not.
>
> Unavoidable rather than a flaw — the columns *are* the storage — but
> `downgrade -1` is a one-keystroke command with an irreversible effect, so it
> is called out here rather than only in the migration's docstring.

After changing `api/tables.py`, autogenerate a revision and **read it before
applying** — autogenerate is a good first draft and a bad final answer,
especially around PostGIS indexes.

### Tests

Most tests run inside the `api` container and need nothing installed locally:

```bash
docker compose exec api python -m pytest          # 272, no network needed
```

`tests/test_api.py` runs on the **host**, hitting the stack over HTTP, because
what it exercises is the wiring. It needs a local virtualenv:

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # Windows
.venv/bin/python     -m pip install -r requirements-dev.txt   # macOS/Linux

.venv/Scripts/python -m pytest tests/test_api.py              # 21, stack must be up
```

It skips itself automatically when the API is not reachable, so `pytest` stays
useful without Docker.

The central one is
`test_solver_output_always_passes_the_independent_checker` — it solves
generated instances across several seeds and sizes and runs the independent
checker over every result.

### Conventions

- **All durations are seconds.** CP-SAT is integer-only; unit drift is a
  silent, expensive bug.
- **Job windows are `timestamptz`; technician shifts are `time`.** A job
  happens on a date; a shift is a recurring daily pattern. Malaysia is UTC+8
  with no DST.
- **Two windows per job.** `hard_window_*` is the SLA — violating it makes a
  schedule invalid. `pref_window_*` is what was promised — missing it is
  penalised.
- **Priority counts DOWN: 1 = highest, 2 = normal, 3 = lowest.** The spec
  never states a direction and the codebase originally had both (the seed
  treated 3 as urgent, the LLM prompt said "3 is most urgent", the column
  defaulted to 1). Settled one way and applied everywhere; migration `0002`
  remaps existing rows with `4 - priority` rather than reinterpreting them,
  so a row that meant "urgent" still means "urgent".
- **Integer primary keys**, so the dispatch parser can handle "job 4412".
- **`POINT(lon lat)`**, written down once in `data/seed/persist.py`.
- **Matrices carry their provenance.** Only `source == "osrm"` is reportable.

---

## Troubleshooting

Things that bite on a fresh clone, in rough order of likelihood.

**`docker compose down` leaves OSRM running.** It is behind a compose profile,
and profiles are opt-in for `down` as well as `up`:

```bash
docker compose --profile osrm down        # stop everything
docker compose --profile osrm down -v     # ...and wipe the database volume
```

**`bash: : command not found` from `build_osrm.sh`.** A Windows clone
converted the script to CRLF and it is being run inside a Linux container.
[.gitattributes](.gitattributes) forces LF for `*.sh`, Dockerfiles and YAML to
prevent this; if you cloned before it existed, `git rm --cached -r . && git
reset --hard` re-normalises.

**Port already in use.** The stack claims 3000, 8000, 5432, 6379 and 5000 —
all common. Postgres on 5432 and anything on 3000 are the usual clashes.
Override per service in a `compose.override.yml`, or stop the other stack.

**`/health/db` returns 503.** The migration has not been applied. Run
`docker compose exec api alembic upgrade head`. The endpoint deliberately fails
rather than reporting healthy on an incomplete schema.

**`/health/routing` says `haversine`.** OSRM is not running, and with
`ROUTING_PROVIDER=auto` everything keeps working while every duration is wrong
by roughly a third. Start it: `docker compose --profile osrm up -d osrm`.
Benchmarks refuse to print a headline in this state, by design.

**Seeding warns that OSRM was unreachable.** Harmless — the day is still
usable — but coordinates were not snapped to roads. Start OSRM and re-seed.

**`build_osrm.sh` downloads ~239 MB** from Geofabrik on first run and needs
roughly 2 GB of free disk for the extract plus the graph. It checks both up
front and refuses early rather than failing halfway.

**Solve returns 0 jobs assigned.** At 80 jobs a 5-second limit is not enough
for CP-SAT to find its first solution; you get the greedy warm start, marked
"not solved" in the UI. Raise the time limit.

**The web UI shows stale behaviour after an edit.** Next dev-server chunks
occasionally do not pick up a change to a bind-mounted file. `docker compose
restart web`.

**Container env changes do not take effect.** `docker compose restart` reuses
the old environment; changing `.env` needs `docker compose up -d api worker`.

---

## Status

All 13 phases built. Verified end to end:

- `docker compose up` brings up api, worker, db/PostGIS, redis, web; osrm is
  profile-gated
- OSRM serves a real, asymmetric travel matrix for KL coordinates
- 40 jobs / 8 technicians seeds and solves to 38/40; result passes the
  independent checker
- map shows one coloured route per technician; timeline shows the day
- dragging a job to another technician re-solves, shows the cost delta, and
  names the customers who would need a phone call
- a typed dispatch change parses, previews, and applies on confirm
- unassigned jobs come with a reason
- benchmark prints the comparison table

Known limits, stated rather than buried:

- **A re-optimised run can read back as "invalid", correctly.** A committed
  re-optimisation stores facts as well as plans — jobs pinned where they
  really happened. A technician who genuinely overran an SLA leaves a stored
  schedule that breaks a hard window, and the independent checker says so on
  every subsequent read. It is *true*, and it is *noisy*: the console's
  `CHECKER: invalid` badge does not distinguish "somebody ran late this
  morning" from "the solver produced nonsense". The fix is to report the two
  separately rather than to soften the checker, which is right as it stands.
  `tests/test_api.py` asserts the distinction the console does not yet make:
  a re-optimised run may carry `window_late`, never a structural violation.
- **Job detail fields cannot be written through the API.** `area`, `address`,
  `phone`, `service_type`, `fault_description` and `notes` are readable via
  `GET /jobs` and populated by the seed, but absent from `JobIn` — because
  `PATCH /jobs/{id}` overwrites every field from the payload, so adding them
  as optional inputs would let a partial update silently null an address by
  omitting it. Fixing it properly means distinguishing "not sent" from "sent
  as null" (pydantic's `model_fields_set`), which is worth doing when
  something actually needs to write them. Nothing does yet.
- **OSRM has no traffic model.** Its car profile applies a fixed 0.8 factor to
  posted limits. Benchmark figures do not model a KL rush hour.
- **Route lines on the map are straight segments between stops**, not driven
  road geometry. Ordering is what a dispatcher reads off the map, and the
  travel *times* are OSRM throughout. Drawing true geometry needs one route
  call per leg.
- **Van stock is boolean**, not counted. A van carrying 2 compressors can
  currently be assigned 3 jobs needing one. The schema stores quantities, so
  this is an additive change.
- **`add_job` from natural language is not supported** — it needs a write the
  preview flow deliberately does not do. Create via `POST /jobs` and re-solve.
- **80 jobs needs roughly 30 seconds.** Below that CP-SAT does not finish its
  first feasible solution and the answer is the greedy warm start. 20- and
  40-job days solve well inside 5 seconds.
- **Small local LLMs are not reliable enough for the parser.** llama3.2 (3B)
  scores 5/7 on `dispatch.evaluate` against `deepseek-chat`'s 7/7. Schema
  validation refused every failure rather than applying a half-specified
  change, so the local model is safe to use — just less useful.
