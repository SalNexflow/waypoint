"""A fast greedy construction heuristic.

Serves two purposes, which is why it lives in `solver/` rather than `bench/`:

  1. **Warm start.** CP-SAT explores from nothing by default, and on a 40-job
     instance it can burn a 5-second limit without finding a single feasible
     solution -- returning UNKNOWN and assigning nobody. Handing it a feasible
     schedule as a hint means the worst case is "no better than greedy"
     instead of "nothing at all".

  2. **Benchmark baseline.** `bench/baseline.py` uses the same function, so
     the thing the solver is measured against is exactly the thing it starts
     from. That makes "the solver improved on it by X" a statement about
     search, not about two different implementations of a heuristic.

Every schedule it produces satisfies all hard constraints: eligibility,
travel, hard windows, shift, and max jobs.
"""

from __future__ import annotations

from solver.problem import Problem, ProblemJob, ProblemTech
from solver.solution import Schedule, Visit


def feasible_append(
    problem: Problem,
    tech: ProblemTech,
    route: list[Visit],
    job: ProblemJob,
    node: int,
    clock: int,
    allowed_overtime_s: int = 0,
) -> Visit | None:
    """Try to add `job` to the end of a route. Returns the Visit, or None."""
    if not problem.can_serve(tech, job):
        return None
    if len(route) >= tech.max_jobs:
        return None

    arrive = clock + problem.travel_s(node, job.node)
    start = max(arrive, job.hard_start_s)
    end = start + job.duration_s

    if start > job.latest_start_s:
        return None
    if end > tech.shift_end_s + allowed_overtime_s:
        return None
    return Visit(job.ref, tech.ref, len(route), arrive, start, end)


def pick_cost(clock: int, visit: Visit) -> int:
    """What taking this job next actually costs: driving PLUS idle waiting.

    Ranking on drive time alone is naive in a way real dispatchers are not: it
    will send someone four minutes down the road to sit outside a locked
    building for two hours. Counting the wait is what a person does
    instinctively, and including it is what keeps the baseline honest rather
    than a strawman.
    """
    return (visit.arrive_s - clock) + visit.wait_s


def greedy_schedule(problem: Problem, allowed_overtime_s: int = 0) -> Schedule:
    """One technician at a time, repeatedly take the cheapest feasible job."""
    remaining = {j.ref for j in problem.jobs}
    visits: list[Visit] = []

    for tech in problem.technicians:
        node = tech.node
        clock = tech.shift_start_s
        route: list[Visit] = []

        while True:
            best: tuple[int, Visit, ProblemJob] | None = None
            for ref in sorted(remaining):
                job = problem.job(ref)
                v = feasible_append(
                    problem, tech, route, job, node, clock, allowed_overtime_s
                )
                if v is None:
                    continue
                c = pick_cost(clock, v)
                if best is None or c < best[0]:
                    best = (c, v, job)
            if best is None:
                break
            _, visit, job = best
            route.append(visit)
            remaining.discard(job.ref)
            node = job.node
            clock = visit.end_s

        visits.extend(route)

    return Schedule(
        day=problem.day,
        visits=tuple(visits),
        unassigned=tuple(sorted(remaining)),
        meta={
            "strategy": "greedy_nn",
            "travel_s": travel_of(problem, visits),
            "matrix_source": problem.travel.source,
            "reportable": problem.travel.is_reportable,
        },
    )


def travel_of(problem: Problem, visits: list[Visit]) -> int:
    total = 0
    by_tech: dict[str, list[Visit]] = {}
    for v in visits:
        by_tech.setdefault(v.technician_ref, []).append(v)
    for tech_ref, route in by_tech.items():
        route.sort(key=lambda v: v.sequence)
        node = problem.tech(tech_ref).node
        for v in route:
            job = problem.job(v.job_ref)
            total += problem.travel_s(node, job.node)
            node = job.node
    return total
