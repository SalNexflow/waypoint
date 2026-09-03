# Field Service Scheduling Optimiser

Dispatch software for companies that send technicians to customer sites.

Given a day's jobs and a team of technicians, produce an assignment and route
for each one that respects skills, time windows, working hours, van stock and
real road travel times. When something changes mid-day — a sick technician, an
overrunning job, an emergency call — re-optimise the remainder of the day
without throwing away work already done.

A dispatcher can also type what happened in plain English and the system turns
that into a constraint change and re-solves.

## Who this is for

Aircon servicing, telco installers, pest control, medical equipment maintenance,
lift servicing. Companies with 5-50 technicians currently running dispatch out of
a WhatsApp group and a spreadsheet.

The value is not "a nicer spreadsheet". It is that a human cannot compute a good
route for 40 jobs across 8 technicians, and the cost of a bad one is paid every
single day in fuel, overtime and missed SLA windows.

## The core problem

This is a Vehicle Routing Problem with Time Windows, plus skill matching. It is
NP-hard. There is no formula — you model it as constraints and let a solver
search.

Model it properly:

**Decision variables** — which technician does which job, and in what order.

**Hard constraints** (a schedule violating these is invalid):
- A job requiring a skill must go to a technician who has it
- A job must start inside its customer time window
- A technician cannot exceed their shift
- A technician cannot be in two places at once — travel time between consecutive
  jobs must fit
- A job needing a part must go to a technician whose van carries it

**Soft constraints** (violations allowed, but penalised in the objective):
- Overtime
- Lateness against the preferred window
- Unassigned jobs (better to leave one unassigned than produce nothing)
- Technician workload imbalance

**Objective** — weighted sum: minimise total travel time, then unassigned jobs,
then overtime, then imbalance. Weights configurable, because different companies
care about different things.

Infeasibility is the normal case, not an error. If 40 jobs cannot fit 8
technicians, the solver must return the best partial schedule and say clearly
which jobs did not fit and why.

## Stack

- Python 3.11+, FastAPI, uvicorn
- OR-Tools CP-SAT for the solver
- OSRM self-hosted, built from an OpenStreetMap extract (Malaysia), for a real
  travel-time matrix — not straight-line distance, not a paid API
- Postgres 16 with PostGIS for jobs, technicians, depots, geometry
- Celery + Redis for solve jobs (a solve takes seconds to minutes, it cannot
  block a request)
- An LLM API for the dispatch parser, behind one module
- Next.js 16, React 19, TypeScript
- MapLibre GL for the map, with route lines and drag-to-reassign
- Docker Compose for all of it

## Shape

```
api/
  main.py                routes only
  config.py              pydantic-settings
  models.py              request/response schemas
  db.py                  async engine, session
  routes/
    jobs.py              CRUD, day view
    technicians.py       CRUD, skills, shifts, van stock
    solve.py             trigger solve, poll status, fetch result
    dispatch.py          natural-language change endpoint
solver/
  problem.py             domain objects -> solver input (pure data, no DB)
  model.py               CP-SAT variables, constraints, objective
  run.py                 solve with time limit, extract solution
  explain.py             why a job was unassigned or moved
  reoptimise.py          re-solve with existing assignments pinned
routing/
  osrm.py                travel-time matrix from OSRM
  cache.py               matrix cache — same coords, same answer
dispatch/
  parse.py               natural language -> structured change
  apply.py               validate and apply the change, then re-solve
worker/
  main.py                celery app
  tasks.py               solve_day, reoptimise_day
web/
  app/
  components/
    DayMap.tsx           MapLibre, routes per technician, colour-coded
    Timeline.tsx         gantt per technician, drag to reassign
    JobPanel.tsx         detail, constraints, why-unassigned
    DispatchBar.tsx      natural-language input
    SolveStatus.tsx      progress, objective value, comparison to previous
tests/
data/
  seed/                  generated realistic day: KL-area jobs, technicians
docker-compose.yml
```

## Data model

**technicians** — id, name, skills[], shift start/end, home location (geography),
van stock (part -> qty), max jobs

**jobs** — id, customer, location (geography), duration, required skills[],
required parts[], time window start/end, priority, status

**depots** — id, location, stocked parts

**solve_runs** — id, date, status, objective value, travel time total, unassigned
count, solver wall time, config snapshot

**assignments** — solve run id, job id, technician id, sequence position,
predicted arrival, predicted departure, pinned (bool)

Geography columns are PostGIS `geography(Point, 4326)`. Distance queries and
depot-radius filtering happen in the database, not in Python.

## Solve flow

1. Load the day: jobs, technicians, depots
2. Build the coordinate list, get the travel-time matrix from OSRM (cache it)
3. Build the CP-SAT model — variables, hard constraints, weighted objective
4. Solve with a wall-clock limit (default 30s, configurable)
5. Extract assignments and predicted timings
6. Store the run with its objective value and metrics
7. For unassigned jobs, run the explainer

The solver must return the best solution found at the time limit, not fail.
Report whether it proved optimality or stopped early.

## Re-optimisation

The hard part, and the thing that makes this real rather than a toy.

Mid-day, some jobs are done, some are in progress, technicians are partway
through routes. Re-solving from scratch would reshuffle everything and send
people back across the city.

So: completed and in-progress jobs are pinned. Remaining jobs re-solve around
them, starting from each technician's current position and current time. Add a
penalty for moving a job that was already communicated to a customer — churn
costs trust, so the objective should prefer leaving things alone unless the gain
is real.

Report the delta: what changed, what it saved.

## Natural-language dispatch

One endpoint. Dispatcher types something like:

- "Ahmad called in sick, redistribute his jobs"
- "Emergency at Wisma Central, needs a chiller tech before 2pm"
- "Job 4412 will overrun by an hour"
- "Siti has to leave at 4 today"

The LLM parses this into a structured change — a typed object, not free text —
against a fixed schema of supported operations: remove technician, add job,
extend duration, change shift, change priority.

Then: validate the change, apply it, re-optimise, and return a summary of what
moved.

Rules:
- The LLM only produces the structured change. It never touches the schedule.
- Unparseable input is rejected with a clear message, not guessed at.
- Every change is previewed before commit — the dispatcher sees the diff and
  confirms.

This layer must be thin and obviously separate from the solver. The solver is
deterministic and testable; the parser is not.

## Benchmarking

Prove the system is worth running.

Generate realistic instances: 20/40/80 jobs, 5/8/15 technicians, KL-area
coordinates, mixed skills and time windows. Then compare:

- **Manual baseline** — greedy nearest-neighbour assignment, roughly what a human
  dispatcher does
- **Solver, 5s / 30s / 120s limits**

Measure: total travel time, jobs assigned, overtime minutes, SLA windows met,
solver wall time.

Report the improvement as a percentage against the baseline. That number is the
whole business case, and it must be honest — same instances, same travel matrix,
same constraints.

Also run a re-optimisation benchmark: inject a disruption at midday, measure
churn and travel-time delta versus a naive full re-solve.

## Definition of done

- `docker compose up` brings up API, worker, Postgres/PostGIS, Redis, OSRM, web
- OSRM serves a real travel-time matrix for KL coordinates
- Seed a day of 40 jobs and 8 technicians
- Solve produces a valid schedule — no constraint violations, verified by an
  independent checker function, not by trusting the solver
- Map shows one coloured route per technician; timeline shows the day
- Drag a job to another technician: it re-solves and shows the cost delta
- Type "Ahmad is sick": it parses, previews the change, re-solves on confirm
- Unassigned jobs come with a reason
- Benchmark script prints the comparison table

## Build order

One phase at a time. Stop after each.

1. Compose stack, config, health checks, PostGIS extension, schema + migrations
2. Seed data generator — realistic KL jobs and technicians
3. OSRM: build the Malaysia extract, matrix client, cache, verified against
   known distances
4. Solver on a tiny instance (5 jobs, 2 technicians) — hard constraints only,
   no objective. Print the schedule as text.
5. Independent feasibility checker — validates a schedule against all constraints
   without using the solver. Write this before trusting any output.
6. Objective function, weights, soft constraints
7. Scale to 40+ jobs, tune time limits, add the explainer
8. Celery worker, solve runs stored, status polling
9. Re-optimisation with pinning and churn penalty
10. Benchmark harness and comparison table
11. Web: map, routes, timeline
12. Drag-to-reassign with delta
13. Natural-language dispatch with preview-and-confirm

## Notes to self

Phase 5 is not optional and not busywork. A solver will happily return a
schedule that violates a constraint you modelled wrong, and it will look
plausible on the map. The independent checker is how you find that. Write it
before you need it.

The travel matrix is the other silent failure. Verify a handful of OSRM
durations against reality before building anything on top — if the matrix is
wrong, every schedule is wrong and nothing about the output will look off.

I write the constraint model in phase 4 and 6 myself. That is the part
interviewers will ask about.
