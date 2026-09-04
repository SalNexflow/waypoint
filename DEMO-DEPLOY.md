# Deploying the demo

**There is no hosted instance, on purpose.** The full system runs locally with
`docker compose up` — see the README. This file is here for the day that
changes, and records what was measured rather than what was assumed.

Only the marketing site is deployed:
<https://waypoint-field.vercel.app>.

---

## Why nothing else is hosted

The expensive piece is OSRM: a 673 MB routing graph held resident in memory,
served from a persistent volume. Nothing free will run that, and it is not
worth paying for so passers-by can click a button.

The rest of the stack *does* fit a free tier, and `render.yaml` is a working
blueprint for it. What stops it being obviously worth doing is measured below.

## What was measured

On the production image under a container constrained to exactly Render's free
web-service limits — `--memory 512m --cpus 0.1` — against the real frozen
matrix and the real demo database:

| | |
|---|---|
| Cold boot to first healthy response | 16 s |
| Loading the 82,656-pair frozen matrix | 1.3 s |
| Memory, idle | 122 MB |
| Memory, peak during a solve | 237 MB — 46% of the cap |
| Serving a stored schedule (8 routes) | 1.6 s |

Memory is not the constraint. The 0.1 CPU is, and it costs about 25×:

| Solve time limit | Result at 0.1 CPU |
|---|---|
| 5 s | **greedy fallback** — CP-SAT returns `UNKNOWN`, 575 travel min |
| 30 s | 574 travel min |
| 60 s | 525 travel min |
| 120 s | 487 travel min |
| *5 s, unconstrained CPU* | *490 travel min* |

The five-second row is the one that matters. Below roughly thirty seconds the
solver finds nothing and the API returns the greedy warm-start — the exact
baseline this project exists to beat. It is flagged (`fell_back: true`, and a
banner in the console since this was measured), but a demo whose headline
result is the baseline is worse than no demo. The console asks for 60 s.

## The free path, if you want it

Render's free web service and Neon's free Postgres, neither of which asks for
a card. **Not** Render's own Postgres: free Render databases expire 30 days
after creation, which would kill the demo a month after setup.

1. **Neon** — create a project. Take the connection string and make two edits
   before giving it to anything: `postgresql://` → `postgresql+asyncpg://`,
   and `?sslmode=require` → `?ssl=require`. The scheme selects the driver;
   asyncpg does not understand `sslmode` and fails at the first query with an
   error that mentions neither problem.

2. **Render** — New → Blueprint → this repository. `render.yaml` configures
   everything except three secrets, which it prompts for:
   `DATABASE_URL`, `DISPATCH_TOKEN`, `DEMO_ACCESS_CODE`.

   `DISPATCH_TOKEN` is not optional. `render.yaml` names two non-local origins
   in `CORS_ORIGINS`, which flips `Settings.dispatch_auth_required` to `True`,
   and without a token every dispatcher route answers 503 — the API refusing
   to serve an open console to the internet rather than logging a warning.

3. **Schema and data**, from a machine with this repo and Docker:

   ```bash
   docker compose run --rm -e DATABASE_URL='postgresql+asyncpg://...?ssl=require' \
       api alembic upgrade head

   docker run --rm -i postgis/postgis:16-3.4 \
       psql 'postgresql://...?sslmode=require' -q < data/demo/demo-data.sql
   ```

   Note the second takes the plain `postgresql://` `sslmode=require` form —
   that is `psql`, not the app. The migration creates the PostGIS extension
   itself; if Neon refuses, run `CREATE EXTENSION IF NOT EXISTS postgis;` in
   its SQL editor first.

4. **Frontends** — `web/` and `mobile/` deploy to Vercel unchanged, but
   `NEXT_PUBLIC_API_BASE` is baked in at build time, so it has to be set to the
   Render URL *at build*, not after. `mobile/` also takes
   `NEXT_PUBLIC_DEMO_CODE`, which must match `DEMO_ACCESS_CODE` on the API.

---

## The two things that will bite you

**Restore the database; never re-seed it.** The frozen matrix was built from
road-**snapped** coordinates, and snapping needs OSRM, which this deployment
does not have. Seeding on the deployed machine produces raw coordinates, of
which only about half land on a point the matrix knows — measured: 20 of 40 —
and every solve then fails with `UnroutableError`. `data/demo/demo-data.sql` is
a `pg_dump` of the seeded, snapped database: 8 technicians, 3 depots, 320 jobs
across 2026-09-03 to 2026-09-10, and the solved schedules for seven of those
days.

Those stored runs matter more than they look. The console's first request is
`/solve/day/{day}/latest`, which reads a saved run rather than solving, so the
page opens on a complete schedule in about a second. Without them it opens on
"No solved schedule for …" and somebody has to press Solve and wait.

2026-09-03 deliberately has **no** stored run: `tests/test_api.py` works on
that day, and its `run_id` fixture would adopt a shipped schedule instead of
building its own.

**Re-seeding and re-freezing go together, in that order.** The seeded window
ends 2026-09-10; after that the console opens on a day with no jobs. Extending
it means regenerating both halves, because the coordinates and the matrix have
to agree:

```bash
docker compose --profile osrm up -d osrm            # the graph is only needed here
docker compose exec api python -m data.seed --jobs 40 --technicians 8 \
    --day 2026-09-11 --seed 7 --jobs-only
docker compose exec api python -m scripts.freeze_matrix --out data/frozen-matrix.json
docker compose exec -T db pg_dump -U waypoint -d waypoint --data-only --no-owner \
    --no-privileges --column-inserts \
    -t depots -t technicians -t jobs -t solve_runs -t assignments \
    > data/demo/demo-data.sql
```

Re-seeding without re-freezing puts jobs at coordinates the matrix has never
seen, and every solve on those days 503s with `UnroutableError` naming them.
That is the intended behaviour — loud and specific rather than quietly
approximated — but it is still an outage.
