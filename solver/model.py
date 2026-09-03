"""CP-SAT model: variables, constraints, objective.

Formulation
-----------
One `AddCircuit` per technician, over that technician's own node set:

    local node 0      the technician's home (depot)
    local nodes 1..K  the jobs that technician could possibly do

"Could possibly do" is doing real work: a job enters a technician's node set
only if the technician has the skills and parts (`Problem.can_serve`) and could
physically reach it inside its window on an otherwise empty day
(`Problem.reachable`). Every pair excluded here is a variable that never
exists, which is the cheapest optimisation available.

Why AddCircuit rather than assignment booleans plus AddNoOverlap: NoOverlap
stops a technician doing two jobs at once but knows nothing about travel, so
it happily ends a job in Klang at 10:00 and starts one in Ampang at 10:00.
AddCircuit gives an ordered route, and the arc literals are what travel time
attaches to. It also eliminates subtours for free, which is the hard part of
hand-rolling a routing model.

Open routes
-----------
AddCircuit produces a *closed* tour, but the phase 1 decision was open routes:
start at home, end at the last job, no return leg. Reconciled by letting the
arc back to the depot exist structurally while contributing zero to both the
time chain and the travel cost. The circuit closes; the working day does not.

Two spec contradictions resolved here
-------------------------------------
1. The spec lists "a technician cannot exceed their shift" as HARD and
   "overtime" as SOFT. Both cannot be true. Resolved as: a hard cap at
   shift_end + allowed_overtime_s, with every second beyond shift_end
   penalised. allowed_overtime_s defaults to 0, which makes the shift purely
   hard; phase 6 raises it.

2. The spec's objective text says "minimise total travel time, then unassigned
   jobs, then overtime" but elsewhere says "better to leave one unassigned
   than produce nothing". As written, the solver would drop a job to save ten
   minutes of driving. Resolved by making the unassigned weight strictly
   dominate: see SolverConfig.
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass, field, replace

from ortools.sat.python import cp_model

from solver.problem import Problem, ProblemJob, ProblemTech
from solver.solution import Schedule, Visit

log = logging.getLogger("waypoint.solver")


@dataclass(frozen=True)
class SolverConfig:
    """Time limit, determinism, and the objective weights.

    Weight units matter and are easy to get wrong, so they are stated here
    once. Travel, overtime and lateness are all measured in SECONDS; the
    unassigned term is a COUNT. That means the weights are doing unit
    conversion as well as expressing preference.

    w_unassigned is deliberately enormous. The largest conceivable total
    travel is every technician driving for their whole shift -- for 15
    technicians on a 9-hour day that is 486,000 seconds. Setting the
    unassigned penalty above that guarantees the solver never drops a job to
    save driving, which is what the spec actually wants even though its
    priority list says otherwise.
    """

    time_limit_s: float = 30.0

    # CP-SAT runs parallel workers by default and they race, so the same model
    # can return different (equally good) answers on different runs. 1 worker
    # is reproducible; the benchmark raises this and reports across seeds.
    workers: int = 1

    allow_unassigned: bool = True
    use_objective: bool = True

    # Hard cap is shift_end + this. 0 makes the shift inviolable.
    allowed_overtime_s: int = 0

    w_travel: int = 1              # per second driven
    w_unassigned: int = 1_000_000  # per job left out -- dominates everything
    w_overtime: int = 20           # per second past shift end
    w_lateness: int = 3            # per second past the preferred window
    # Imbalance couples every technician to every other one, which wrecks the
    # solver's ability to work on them semi-independently. Off by default;
    # turn it on last and measure what it costs.
    w_imbalance: int = 0
    # Phase 9: penalty per job moved off the technician it was promised to.
    w_churn: int = 0

    log_search: bool = False

    def as_dict(self) -> dict:
        """Snapshot for solve_runs.config_snapshot, so a stored run stays
        interpretable months later."""
        return {
            "time_limit_s": self.time_limit_s,
            "workers": self.workers,
            "allow_unassigned": self.allow_unassigned,
            "use_objective": self.use_objective,
            "allowed_overtime_s": self.allowed_overtime_s,
            "w_travel": self.w_travel,
            "w_unassigned": self.w_unassigned,
            "w_overtime": self.w_overtime,
            "w_lateness": self.w_lateness,
            "w_imbalance": self.w_imbalance,
            "w_churn": self.w_churn,
        }


@dataclass(frozen=True)
class Pin:
    """A job held fixed during re-optimisation (phase 9).

    start_s=None pins only the technician, leaving the time free to move --
    right for a job already promised to a customer but not yet started.
    A start_s pins both, which is what a job in progress needs.
    """

    job_ref: str
    technician_ref: str
    start_s: int | None = None


@dataclass
class _TechBlock:
    """Per-technician model fragment. Internal bookkeeping."""

    tech: ProblemTech
    jobs: list[ProblemJob] = field(default_factory=list)
    arcs: dict[tuple[int, int], object] = field(default_factory=dict)
    visit: dict[str, object] = field(default_factory=dict)
    unused: object = None


def solve(
    problem: Problem,
    config: SolverConfig | None = None,
    pins: list[Pin] | None = None,
    previous: dict[str, str] | None = None,
    require: list[str] | None = None,
    hint: Schedule | None = None,
    not_before: int | None = None,
    exclude_technicians: set[str] | None = None,
    exclude_jobs: set[str] | None = None,
    warm_start: bool = True,
) -> Schedule:
    """Build and solve the model, returning the best schedule found.

    Never raises on an over-constrained problem: an infeasible or timed-out
    solve returns an empty Schedule with every job unassigned and the reason
    in `meta`. The spec is explicit that infeasibility is the normal case, and
    the caller needs a Schedule to explain, not an exception.
    """
    cfg = config or SolverConfig()
    pins = pins or []
    previous = previous or {}
    require = set(require or ())
    excluded = set(exclude_technicians or ())
    # Jobs that still occupy a node (the travel matrix is indexed by node, and
    # Problem enforces contiguous job nodes, so they cannot simply be dropped)
    # but must not be scheduled. Cancelled work, mainly.
    excluded_jobs = set(exclude_jobs or ())
    started = _time.perf_counter()

    model = cp_model.CpModel()

    # ---- Start-time variables, one per job -------------------------------
    # The domain IS a constraint: [hard_start, latest_start] tells CP-SAT
    # before search begins that the job cannot begin outside its window. Every
    # value excluded here is one the search never explores.
    start: dict[str, object] = {}
    for j in problem.jobs:
        lo, hi = j.hard_start_s, j.latest_start_s
        if hi < lo and j.ref not in excluded_jobs:
            # Window shorter than the job. Would make the whole model
            # infeasible with nothing pointing at the cause, so bail loudly.
            return _failed(
                problem,
                cfg,
                started,
                f"job {j.ref} has a {(j.hard_end_s - j.hard_start_s) // 60}min "
                f"window but takes {j.duration_s // 60}min",
            )
        # An excluded job is forced unassigned, so its start variable is never
        # meaningful -- give it a legal one-value domain rather than an empty
        # one, which would make the whole model infeasible.
        if hi < lo:
            hi = lo
        start[j.ref] = model.NewIntVar(lo, hi, f"start_{j.ref}")

    # ---- Per-job unassigned indicator ------------------------------------
    unassigned: dict[str, object] = {
        j.ref: model.NewBoolVar(f"unassigned_{j.ref}") for j in problem.jobs
    }

    # ---- Build one circuit per technician --------------------------------
    blocks: list[_TechBlock] = []
    horizon = max((j.hard_end_s for j in problem.jobs), default=86_400)

    for t in problem.technicians:
        block = _TechBlock(tech=t)
        if t.ref in excluded:
            # Off sick, or otherwise unavailable. "Unavailable" means no NEW
            # work -- not that their morning never happened. Any job already
            # pinned to them (done, or under way) stays a candidate so it
            # remains in the schedule; nothing else does.
            #
            # Removing completed work entirely would misrepresent the day:
            # the customer was visited, the van was there, and the technician
            # is not at their home depot any more.
            still_theirs = {
                p.job_ref for p in pins if p.technician_ref == t.ref
            }
            if not still_theirs:
                blocks.append(block)
                continue
            block.jobs = [j for j in problem.jobs if j.ref in still_theirs]
            local = {j.ref: k + 1 for k, j in enumerate(block.jobs)}
            for j in block.jobs:
                block.visit[j.ref] = model.NewBoolVar(f"visit_{t.ref}_{j.ref}")
            block.unused = model.NewBoolVar(f"unused_{t.ref}")
            arcs = [(0, 0, block.unused)]
            for j in block.jobs:
                k = local[j.ref]
                v = block.visit[j.ref]
                arcs.append((k, k, v.Not()))
                a_out = model.NewBoolVar(f"arc_{t.ref}_D_{j.ref}")
                arcs.append((0, k, a_out))
                block.arcs[(0, k)] = a_out
                model.Add(
                    start[j.ref]
                    >= t.shift_start_s + problem.travel_s(t.node, j.node)
                ).OnlyEnforceIf(a_out)
                a_in = model.NewBoolVar(f"arc_{t.ref}_{j.ref}_D")
                arcs.append((k, 0, a_in))
                block.arcs[(k, 0)] = a_in
                model.AddImplication(block.unused, v.Not())
            for a in block.jobs:
                for b in block.jobs:
                    if a.ref == b.ref:
                        continue
                    drive = problem.travel_s(a.node, b.node)
                    lit = model.NewBoolVar(f"arc_{t.ref}_{a.ref}_{b.ref}")
                    arcs.append((local[a.ref], local[b.ref], lit))
                    block.arcs[(local[a.ref], local[b.ref])] = lit
                    model.Add(
                        start[b.ref] >= start[a.ref] + a.duration_s + drive
                    ).OnlyEnforceIf(lit)
            model.AddCircuit(arcs)
            blocks.append(block)
            continue
        block.jobs = [
            j
            for j in problem.jobs
            if j.ref not in excluded_jobs
            and problem.can_serve(t, j)
            and problem.reachable(t, j)
        ]
        if not block.jobs:
            blocks.append(block)
            continue

        local = {j.ref: k + 1 for k, j in enumerate(block.jobs)}  # 0 is the depot

        for j in block.jobs:
            block.visit[j.ref] = model.NewBoolVar(f"visit_{t.ref}_{j.ref}")

        # "This technician is not used at all" -- the depot self-loop.
        # AddCircuit requires every node to have either a real incoming arc or
        # a self-loop, the depot included.
        block.unused = model.NewBoolVar(f"unused_{t.ref}")

        arcs: list[tuple[int, int, object]] = [(0, 0, block.unused)]

        for j in block.jobs:
            k = local[j.ref]
            v = block.visit[j.ref]

            # Self-loop == not visited by this technician.
            arcs.append((k, k, v.Not()))

            # Depot -> job. Departing home at shift start.
            a_out = model.NewBoolVar(f"arc_{t.ref}_D_{j.ref}")
            arcs.append((0, k, a_out))
            block.arcs[(0, k)] = a_out
            model.Add(
                start[j.ref] >= t.shift_start_s + problem.travel_s(t.node, j.node)
            ).OnlyEnforceIf(a_out)

            # Job -> depot. Structural only: zero time, zero cost. This is
            # what turns AddCircuit's closed tour into an open route.
            a_in = model.NewBoolVar(f"arc_{t.ref}_{j.ref}_D")
            arcs.append((k, 0, a_in))
            block.arcs[(k, 0)] = a_in

            # If the technician is unused, nobody is visited.
            model.AddImplication(block.unused, v.Not())

        # Job -> job arcs, pruned by time feasibility.
        for a in block.jobs:
            ka = local[a.ref]
            earliest_a = max(
                a.hard_start_s, t.shift_start_s + problem.travel_s(t.node, a.node)
            )
            for b in block.jobs:
                if a.ref == b.ref:
                    continue
                kb = local[b.ref]
                drive = problem.travel_s(a.node, b.node)
                # If starting a as early as possible still cannot reach b in
                # time, this arc can never be true. Skip it entirely.
                if earliest_a + a.duration_s + drive > b.latest_start_s:
                    continue
                lit = model.NewBoolVar(f"arc_{t.ref}_{a.ref}_{b.ref}")
                arcs.append((ka, kb, lit))
                block.arcs[(ka, kb)] = lit
                model.Add(
                    start[b.ref] >= start[a.ref] + a.duration_s + drive
                ).OnlyEnforceIf(lit)

        model.AddCircuit(arcs)

        # Shift: every visited job must finish by the hard cap.
        cap = t.shift_end_s + cfg.allowed_overtime_s
        for j in block.jobs:
            model.Add(start[j.ref] + j.duration_s <= cap).OnlyEnforceIf(
                block.visit[j.ref]
            )

        # Workload cap.
        if t.max_jobs < len(block.jobs):
            model.Add(sum(block.visit.values()) <= t.max_jobs)

        blocks.append(block)

    # ---- Each job goes to exactly one technician, or to nobody -----------
    for j in problem.jobs:
        options = [b.visit[j.ref] for b in blocks if j.ref in b.visit]
        model.AddExactlyOne([*options, unassigned[j.ref]])
        if j.ref in excluded_jobs:
            # Cancelled: forced out regardless of allow_unassigned or require.
            model.Add(unassigned[j.ref] == 1)
            continue
        if not cfg.allow_unassigned or j.ref in require:
            model.Add(unassigned[j.ref] == 0)

    # ---- Nothing unpinned may be scheduled in the past --------------------
    # Re-optimisation happens mid-day. Work already done or under way is
    # pinned; everything else has to be schedulable from now onward, or the
    # solver would cheerfully "plan" a job for 9am at 2pm.
    if not_before is not None:
        pinned_refs = {p.job_ref for p in pins}
        for j in problem.jobs:
            if j.ref not in pinned_refs and not_before > j.hard_start_s:
                if not_before > j.latest_start_s:
                    # Cannot start it at all any more; force it out rather than
                    # leave an unsatisfiable domain.
                    model.Add(unassigned[j.ref] == 1)
                else:
                    model.Add(start[j.ref] >= not_before)

    # ---- Pinning (phase 9) -----------------------------------------------
    for pin in pins:
        block = next((b for b in blocks if b.tech.ref == pin.technician_ref), None)
        if block is None or pin.job_ref not in block.visit:
            return _failed(
                problem,
                cfg,
                started,
                f"cannot pin {pin.job_ref} to {pin.technician_ref}: "
                "that technician is not eligible for it",
            )
        model.Add(block.visit[pin.job_ref] == 1)
        if pin.start_s is not None:
            model.Add(start[pin.job_ref] == pin.start_s)

    # ---- Objective --------------------------------------------------------
    terms = []
    travel_terms = []
    for b in blocks:
        for (a_local, b_local), lit in b.arcs.items():
            if a_local == 0:
                drive = problem.travel_s(b.tech.node, b.jobs[b_local - 1].node)
            elif b_local == 0:
                drive = 0  # open route: the return leg is free
            else:
                drive = problem.travel_s(
                    b.jobs[a_local - 1].node, b.jobs[b_local - 1].node
                )
            if drive:
                travel_terms.append(drive * lit)

    overtime_vars = []
    load_vars = []
    for b in blocks:
        if not b.jobs:
            continue
        t = b.tech
        cap = t.shift_end_s + cfg.allowed_overtime_s
        day_end = model.NewIntVar(t.shift_start_s, cap, f"dayend_{t.ref}")
        for j in b.jobs:
            model.Add(day_end >= start[j.ref] + j.duration_s).OnlyEnforceIf(
                b.visit[j.ref]
            )
        ot = model.NewIntVar(0, max(cfg.allowed_overtime_s, 0), f"ot_{t.ref}")
        model.Add(ot >= day_end - t.shift_end_s)
        overtime_vars.append(ot)

        load = model.NewIntVar(0, t.shift_seconds + cfg.allowed_overtime_s,
                               f"load_{t.ref}")
        model.Add(load == sum(j.duration_s * b.visit[j.ref] for j in b.jobs))
        load_vars.append(load)

    lateness_vars = []
    for j in problem.jobs:
        if j.pref_end_s is None:
            continue
        late = model.NewIntVar(0, horizon, f"late_{j.ref}")
        model.Add(late >= start[j.ref] - j.pref_end_s).OnlyEnforceIf(
            unassigned[j.ref].Not()
        )
        lateness_vars.append(late)

    churn_terms = []
    if cfg.w_churn and previous:
        for j in problem.jobs:
            prev_tech = previous.get(j.ref)
            if prev_tech is None:
                continue
            moved = model.NewBoolVar(f"moved_{j.ref}")
            block = next((b for b in blocks if b.tech.ref == prev_tech), None)
            if block is not None and j.ref in block.visit:
                # moved == not still with the original technician
                model.Add(moved == 1 - block.visit[j.ref])
            else:
                model.Add(moved == 1)
            churn_terms.append(moved)

    if cfg.use_objective:
        obj = []
        if travel_terms and cfg.w_travel:
            obj.append(cfg.w_travel * sum(travel_terms))
        if cfg.w_unassigned:
            obj.append(cfg.w_unassigned * sum(unassigned.values()))
        if overtime_vars and cfg.w_overtime:
            obj.append(cfg.w_overtime * sum(overtime_vars))
        if lateness_vars and cfg.w_lateness:
            obj.append(cfg.w_lateness * sum(lateness_vars))
        if churn_terms and cfg.w_churn:
            obj.append(cfg.w_churn * sum(churn_terms))
        if load_vars and cfg.w_imbalance and len(load_vars) > 1:
            hi = model.NewIntVar(0, 86_400, "load_max")
            lo = model.NewIntVar(0, 86_400, "load_min")
            model.AddMaxEquality(hi, load_vars)
            model.AddMinEquality(lo, load_vars)
            obj.append(cfg.w_imbalance * (hi - lo))
        if obj:
            model.Minimize(sum(obj))

    # ---- Warm start -------------------------------------------------------
    # A hint is a suggested starting assignment, not a constraint: CP-SAT is
    # free to ignore or improve on it. It matters enormously for two callers.
    #
    # The explainer re-solves with one job forced in and needs the *rest* of
    # the day to stay put; without a hint a short probe wanders off and
    # produces a much worse schedule, making the reported trade-off an
    # artifact of the time budget rather than a real cost.
    #
    # Re-optimisation (phase 9) has the same need for the same reason.
    # With no caller-supplied hint, construct one greedily. Measured: on a
    # 40-job instance a 5-second limit could expire before CP-SAT found ANY
    # feasible solution, returning UNKNOWN and assigning nobody. Starting from
    # a feasible schedule makes the worst case "no better than greedy" rather
    # than "nothing at all". Skipped when jobs are pinned or excluded, since
    # the greedy schedule would not respect those.
    fallback: Schedule | None = None
    if hint is None and warm_start and not pins and not excluded and not_before is None:
        from solver.greedy import greedy_schedule

        candidate = greedy_schedule(problem, cfg.allowed_overtime_s)
        if candidate.visits:
            hint = candidate
            # Only usable as a returnable answer if nothing was demanded of
            # this solve that greedy does not know about.
            if not require:
                fallback = candidate

    if hint is not None:
        by_job = {v.job_ref: v for v in hint.visits}
        for j in problem.jobs:
            v = by_job.get(j.ref)
            if v is None:
                model.AddHint(unassigned[j.ref], 1)
                continue
            model.AddHint(unassigned[j.ref], 0)
            lo, hi = j.hard_start_s, j.latest_start_s
            if lo <= v.start_s <= hi:
                model.AddHint(start[j.ref], v.start_s)
            for b in blocks:
                if j.ref in b.visit:
                    model.AddHint(b.visit[j.ref], 1 if b.tech.ref == v.technician_ref else 0)

        # Hint the ARCS too, not just which technician has which job.
        #
        # This is the difference between a hint CP-SAT can accept and one it
        # has to finish solving. AddCircuit's real variables are the arc
        # literals; `visit` and `start` are consequences of them. A hint that
        # leaves every arc unset says "these are the right assignments, now
        # work out the routes yourself" -- which is most of the problem.
        # Measured on 80 jobs / 15 technicians: without arc hints a 5-second
        # solve returned UNKNOWN with nothing assigned on all three seeds.
        #
        # An arc is hinted 1 only if it is a consecutive pair in the hinted
        # route AND survived pruning. If any pair is missing, the hint for
        # that technician would be structurally incoherent, so the whole
        # block is left unhinted rather than half-hinted.
        for b in blocks:
            if not b.jobs:
                continue
            route = sorted(
                (v for v in hint.visits if v.technician_ref == b.tech.ref),
                key=lambda v: v.sequence,
            )
            local = {j.ref: k + 1 for k, j in enumerate(b.jobs)}
            if b.unused is not None:
                model.AddHint(b.unused, 0 if route else 1)
            if not route:
                for lit in b.arcs.values():
                    model.AddHint(lit, 0)
                continue

            path = [0, *(local[v.job_ref] for v in route if v.job_ref in local), 0]
            if len(path) != len(route) + 2:
                continue  # a hinted job this technician cannot serve; skip
            wanted = set(zip(path, path[1:]))
            if not wanted <= set(b.arcs):
                continue  # a needed arc was pruned away; leave this one alone
            for key, lit in b.arcs.items():
                model.AddHint(lit, 1 if key in wanted else 0)

    # ---- Solve ------------------------------------------------------------
    invalid = model.Validate()
    if invalid:
        return _failed(problem, cfg, started, f"model invalid: {invalid[:300]}")

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = cfg.time_limit_s
    solver.parameters.num_search_workers = cfg.workers
    solver.parameters.log_search_progress = cfg.log_search
    status = solver.Solve(model)
    wall_ms = int((_time.perf_counter() - started) * 1000)
    status_name = solver.StatusName(status)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # INFEASIBLE is a proof that nothing works. UNKNOWN means the time
        # limit ran out before anything was found. Very different diagnoses,
        # so the status name is carried through rather than flattened.
        #
        # On UNKNOWN, hand back the greedy schedule we started from rather
        # than an empty day. It satisfies every hard constraint and goes
        # through the same checker; the alternative is telling a dispatcher
        # that none of their 80 jobs can be done, which is false. The meta
        # says plainly that no search result is in here, so nothing
        # downstream can mistake it for a solve.
        if status == cp_model.UNKNOWN and fallback is not None:
            return replace(
                fallback,
                meta={
                    **fallback.meta,
                    "status": status_name,
                    "proved_optimal": False,
                    "objective": None,
                    "wall_ms": wall_ms,
                    "fell_back": True,
                    "reason": (
                        "time limit expired before CP-SAT found any solution; "
                        "returning the greedy warm-start schedule unimproved"
                    ),
                },
            )
        return _failed(problem, cfg, started, status_name, status_name=status_name)

    # ---- Extract ----------------------------------------------------------
    visits: list[Visit] = []
    for b in blocks:
        if not b.jobs or solver.BooleanValue(b.unused):
            continue
        order = _walk_circuit(solver, b)
        node = b.tech.node
        clock = b.tech.shift_start_s
        for seq, job in enumerate(order):
            drive = problem.travel_s(node, job.node)
            arrive = clock + drive
            st = solver.Value(start[job.ref])
            visits.append(
                Visit(
                    job_ref=job.ref,
                    technician_ref=b.tech.ref,
                    sequence=seq,
                    arrive_s=arrive,
                    start_s=st,
                    end_s=st + job.duration_s,
                )
            )
            node = job.node
            clock = st + job.duration_s

    left = tuple(
        sorted(j.ref for j in problem.jobs if solver.BooleanValue(unassigned[j.ref]))
    )

    travel_total = sum(
        problem.travel_s(
            b.tech.node if v.sequence == 0 else problem.job(prev).node, problem.job(v.job_ref).node
        )
        for b in blocks
        for prev, v in _consecutive(visits, b.tech.ref)
    )

    return Schedule(
        day=problem.day,
        visits=tuple(visits),
        unassigned=left,
        meta={
            "status": status_name,
            "proved_optimal": status == cp_model.OPTIMAL,
            "objective": int(solver.ObjectiveValue()) if cfg.use_objective else None,
            "best_bound": int(solver.BestObjectiveBound()) if cfg.use_objective else None,
            "wall_ms": wall_ms,
            "solver_wall_s": round(solver.WallTime(), 3),
            "travel_s": travel_total,
            "matrix_source": problem.travel.source,
            "reportable": problem.travel.is_reportable,
            "workers": cfg.workers,
            "variables": len(model.Proto().variables),
            "constraints": len(model.Proto().constraints),
        },
    )


def _consecutive(visits: list[Visit], tech_ref: str):
    """Yield (previous_job_ref_or_None, visit) along one technician's route."""
    route = sorted(
        (v for v in visits if v.technician_ref == tech_ref), key=lambda v: v.sequence
    )
    prev = None
    for v in route:
        yield prev, v
        prev = v.job_ref


def _walk_circuit(solver, block: _TechBlock) -> list[ProblemJob]:
    """Follow the selected arcs from the depot to recover the visit order."""
    order: list[ProblemJob] = []
    cur = 0
    seen = set()
    while True:
        nxt = None
        for (a, b), lit in block.arcs.items():
            if a == cur and b != cur and solver.BooleanValue(lit):
                nxt = b
                break
        if nxt is None or nxt == 0:
            break
        if nxt in seen:  # defensive: AddCircuit should make this impossible
            log.warning("circuit revisited node %s for %s", nxt, block.tech.ref)
            break
        seen.add(nxt)
        order.append(block.jobs[nxt - 1])
        cur = nxt
    return order


def _failed(
    problem: Problem,
    cfg: SolverConfig,
    started: float,
    reason: str,
    status_name: str = "MODEL_ERROR",
) -> Schedule:
    return Schedule(
        day=problem.day,
        visits=(),
        unassigned=tuple(j.ref for j in problem.jobs),
        meta={
            "status": status_name,
            "proved_optimal": False,
            "objective": None,
            "wall_ms": int((_time.perf_counter() - started) * 1000),
            "reason": reason,
            "matrix_source": problem.travel.source,
            "reportable": problem.travel.is_reportable,
        },
    )
