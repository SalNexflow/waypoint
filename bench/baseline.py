"""Baselines: what a human dispatcher actually produces.

Two of them, deliberately.

**Greedy nearest-neighbour** is the obvious baseline and the one the spec
names. It is also too weak to be honest. A real dispatcher is not purely
greedy -- they carve the city into patches, give each technician a patch, and
work through it. Benchmarking only against pure NN inflates the solver's
apparent improvement, and the spec is explicit that the number "must be
honest".

**Cluster-then-nearest-neighbour** is the fair comparison: assign each
technician a geographic region by k-means on the job coordinates, then run
nearest-neighbour inside each region. That is much closer to what a competent
human does with a whiteboard, and it is a materially harder number to beat.

Both baselines respect every hard constraint. A baseline that cheated would
make the comparison meaningless in the other direction.
"""

from __future__ import annotations

import math

from solver.greedy import (
    feasible_append as _feasible_append,
)
from solver.greedy import (
    greedy_schedule as greedy_nearest_neighbour,
)
from solver.greedy import (
    pick_cost,
    travel_of as _travel_of,
)
from solver.problem import Problem, ProblemJob, ProblemTech
from solver.solution import Schedule, Visit


def _kmeans(points: list[tuple[float, float]], k: int, iters: int = 40, seed: int = 0):
    """Tiny k-means. No sklearn -- this is 20 lines and one less dependency.

    Deterministic: centroids are seeded by spreading over sorted points rather
    than at random, so the baseline is as reproducible as the solver.
    """
    if not points or k <= 0:
        return []
    k = min(k, len(points))
    ordered = sorted(points)
    centroids = [ordered[i * len(ordered) // k] for i in range(k)]

    for _ in range(iters):
        buckets: list[list[tuple[float, float]]] = [[] for _ in range(k)]
        for p in points:
            i = min(
                range(k),
                key=lambda c: (p[0] - centroids[c][0]) ** 2
                + (p[1] - centroids[c][1]) ** 2,
            )
            buckets[i].append(p)
        moved = False
        for i, b in enumerate(buckets):
            if not b:
                continue
            nc = (sum(x for x, _ in b) / len(b), sum(y for _, y in b) / len(b))
            if nc != centroids[i]:
                centroids[i] = nc
                moved = True
        if not moved:
            break
    return centroids


def cluster_then_nearest_neighbour(
    problem: Problem, allowed_overtime_s: int = 0
) -> Schedule:
    """Carve the city into regions, one per technician, then NN within each.

    This is the honest baseline. A dispatcher with a map and a marker does
    approximately this, and it is a much better plan than pure greedy.
    """
    coords = [(j.lat, j.lon) for j in problem.jobs]
    centroids = _kmeans(coords, problem.n_techs)
    if not centroids:
        return greedy_nearest_neighbour(problem, allowed_overtime_s)

    # Assign each cluster to the technician whose home is nearest to it, so
    # regions match where people actually start their day.
    unclaimed = list(range(len(centroids)))
    region_of: dict[str, int] = {}
    for tech in problem.technicians:
        if not unclaimed:
            break
        best = min(
            unclaimed,
            key=lambda c: (tech.lat - centroids[c][0]) ** 2
            + (tech.lon - centroids[c][1]) ** 2,
        )
        region_of[tech.ref] = best
        unclaimed.remove(best)

    def cluster_of(job: ProblemJob) -> int:
        return min(
            range(len(centroids)),
            key=lambda c: (job.lat - centroids[c][0]) ** 2
            + (job.lon - centroids[c][1]) ** 2,
        )

    job_cluster = {j.ref: cluster_of(j) for j in problem.jobs}
    remaining = {j.ref for j in problem.jobs}
    visits: list[Visit] = []

    # Two passes: each technician works their own region first, then anyone
    # with time left picks up whatever is stranded. The second pass is what a
    # dispatcher does at 11am when someone finishes early.
    for pass_no in (0, 1):
        for tech in problem.technicians:
            mine = region_of.get(tech.ref)
            existing = [v for v in visits if v.technician_ref == tech.ref]
            node = (
                problem.job(existing[-1].job_ref).node if existing else tech.node
            )
            clock = existing[-1].end_s if existing else tech.shift_start_s
            route = list(existing)

            while True:
                best: tuple[int, Visit, ProblemJob] | None = None
                for ref in sorted(remaining):
                    if pass_no == 0 and job_cluster[ref] != mine:
                        continue
                    job = problem.job(ref)
                    v = _feasible_append(
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
                visits.append(visit)
                remaining.discard(job.ref)
                node = job.node
                clock = visit.end_s

    return Schedule(
        day=problem.day,
        visits=tuple(visits),
        unassigned=tuple(sorted(remaining)),
        meta={
            "strategy": "cluster_nn",
            "travel_s": _travel_of(problem, visits),
            "matrix_source": problem.travel.source,
            "reportable": problem.travel.is_reportable,
        },
    )


BASELINES = {
    "greedy_nn": greedy_nearest_neighbour,
    "cluster_nn": cluster_then_nearest_neighbour,
}
