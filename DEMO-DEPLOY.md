# Deploying the demo

The cut-down deployment: a frozen travel matrix instead of a live OSRM, inline
solving instead of Celery, and therefore no Redis and no routing engine. About
$7/month, against roughly $70 for the full stack.

What is already done and what is not:

| | |
|---|---|
| Marketing site | **live** — https://waypoint-field.vercel.app |
| Dispatcher console | **live** — https://waypoint-console.vercel.app |
| Technician PWA | **live** — https://waypoint-technician.vercel.app |
| API | **not deployed** — needs a Fly.io account |
| Database | **not deployed** — needs a Neon account |

Both frontends are already built against `https://waypoint-dispatch-demo.fly.dev`.
They render, and every request fails, until the API below exists at that
hostname. **If you use a different Fly app name, the frontends must be
redeployed**, because `NEXT_PUBLIC_API_BASE` is baked in at build time.

---

## The one thing that will bite you

The frozen matrix was built from **road-snapped** coordinates. Snapping needs
OSRM, which this deployment does not have. So the demo database cannot be
seeded with `python -m data.seed` on the deployed machine — the generator would
produce raw coordinates, only about half of which land on a point the frozen
matrix knows, and every solve would fail with `UnroutableError`.

Restore `data/demo/demo-data.sql` instead. It is a `pg_dump` of the seeded,
snapped database: 8 technicians, 3 depots, and 320 jobs across 2026-09-03 to
2026-09-10. That is what makes the coordinates match the matrix exactly.

---

## 1. Database (Neon)

Create a project at https://neon.tech — the free tier is enough. Then, in the
Neon SQL editor:

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
```

Neon hands you a connection string for `psql`. This app talks **asyncpg**, so
two edits are needed before it will work:

```
# What Neon gives you:
postgresql://user:pass@ep-xxx.ap-southeast-1.aws.neon.tech/waypoint?sslmode=require

# What this app needs:
postgresql+asyncpg://user:pass@ep-xxx.ap-southeast-1.aws.neon.tech/waypoint?ssl=require
#         ^^^^^^^^                                                          ^^^
```

`postgresql+asyncpg://` selects the driver; asyncpg spells the TLS option
`ssl=require` and does not understand `sslmode`. Passing Neon's string through
unedited fails at the first query with a driver error that does not mention
either problem.

## 2. API (Fly.io)

`flyctl` is already installed at `~/.fly/bin/flyctl`.

```bash
export PATH="$HOME/.fly/bin:$PATH"
flyctl auth login                      # opens a browser

cd /path/to/Waypoint
flyctl launch --no-deploy --copy-config --name waypoint-dispatch-demo --region sin
```

Set both secrets **before** the first deploy:

```bash
flyctl secrets set \
  DATABASE_URL='postgresql+asyncpg://user:pass@ep-xxx.../waypoint?ssl=require' \
  DISPATCH_TOKEN='<the token from the deploy notes>'
```

`DISPATCH_TOKEN` is not optional here. `fly.toml` names two Vercel origins in
`CORS_ORIGINS`, and `Settings.dispatch_auth_required` flips to `True` the moment
any non-local origin is trusted. Without a token the dispatcher routes answer
**503**, by design — the API refuses to serve an open console to the internet
rather than logging a warning nobody reads.

```bash
flyctl deploy
```

## 3. Schema and data

```bash
flyctl ssh console -C "alembic upgrade head"
```

Then restore the demo data. `psql` is not in the API image, so pipe it from
anywhere that has one — including the local compose stack:

```bash
docker compose exec -T db psql '<the psql-form Neon URL>' -q -f - < data/demo/demo-data.sql
```

Note that is the **`postgresql://` `sslmode=require`** form, not the asyncpg
one — this is `psql`, not the app.

## 4. Verify

```bash
curl -s https://waypoint-dispatch-demo.fly.dev/health
curl -s https://waypoint-dispatch-demo.fly.dev/health/routing
```

`/health/routing` should report:

```json
{
  "status": "ok",
  "configured": "frozen",
  "source": "osrm",
  "reportable": true,
  "frozen": { "pairs": 82656, "graph": "klang-valley" }
}
```

`source: "osrm"` with no OSRM running is correct and not a bug: the bundle
records where its numbers came from, and they came from OSRM. Freeze a
haversine matrix instead and this reports `haversine` with `reportable: false`.

Dispatcher routes should 401/403 without a token and answer with it:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://waypoint-dispatch-demo.fly.dev/jobs?day=2026-09-04
curl -s -H "Authorization: Bearer $DISPATCH_TOKEN" \
  'https://waypoint-dispatch-demo.fly.dev/jobs?day=2026-09-04' | head -c 200
```

## 5. Unlock the console

Open https://waypoint-console.vercel.app. It will ask for the dispatch token —
paste the same value set as the Fly secret. It is kept in `localStorage` and
sent as a bearer token on every request; it is never baked into the build, so
rotating it means setting a new Fly secret and pasting the new value once.

For the technician PWA, mint an access code from the console's access screen
and redeem it at https://waypoint-technician.vercel.app.

---

## Refreshing the demo

The seeded window runs **2026-09-03 to 2026-09-10**. The console opens on
today in `Asia/Kuala_Lumpur`, so after 2026-09-10 it opens on a day with no
jobs — not broken, just empty.

Extending it means regenerating both halves together, because the matrix and
the coordinates have to agree:

```bash
docker compose --profile osrm up -d osrm            # the graph is only needed here
docker compose exec api python -m data.seed --jobs 40 --technicians 8 \
    --day 2026-09-11 --seed 7 --jobs-only
docker compose exec api python -m scripts.freeze_matrix --out data/frozen-matrix.json
docker compose exec -T db pg_dump -U waypoint -d waypoint --data-only --no-owner \
    --no-privileges --column-inserts -t depots -t technicians -t jobs \
    > data/demo/demo-data.sql
flyctl deploy                                        # ships the new matrix
```

Re-seeding and forgetting to re-freeze is the failure this ordering exists to
prevent: the new jobs would be at coordinates the matrix has never seen, and
every solve on those days would 503 with `UnroutableError` naming them. That is
the intended behaviour — loud and specific rather than quietly approximated —
but it is still an outage, so run the two commands together.
