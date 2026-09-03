"""Benchmark harness: is this worth running?

Prints the comparison table that is the whole business case. Its one job is to
be honest, which means several things it would be easy to skip:

  * **The same instances for everybody.** Baselines and solver see identical
    jobs, identical technicians, identical travel matrix. Anything else and
    the comparison is theatre.

  * **Every schedule is checked.** A baseline or a solver run that violates a
    constraint is excluded from the averages and reported as a failure. An
    invalid schedule with low travel time is not a better answer.

  * **OSRM or it does not count.** Haversine matrices carry
    `is_reportable=False`, and this refuses to print a headline improvement
    from them. A provisional number that looks like a result is worse than no
    number.

  * **Both baselines.** Pure greedy nearest-neighbour flatters the solver.
    Cluster-then-NN is much closer to what a human dispatcher does, and the
    improvement against it is the number to quote.

  * **Free-flow travel.** OSRM's car profile has no traffic model. Stated in
    the output rather than left for someone to discover.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import time
from dataclasses import dataclass, field
from datetime import date

from bench.baseline import BASELINES
from data.seed.generate import generate_instance, snap_instance
from routing import build_provider
from solver.check import check
from solver.model import SolverConfig, solve
from solver.problem import Problem, from_seed_instance, seed_coords
from solver.reoptimise import Disruption, reoptimise
from solver.solution import Schedule

DAY = date(2026, 9, 3)
OSRM_URL = os.environ.get("OSRM_URL", "http://osrm:5000")


@dataclass
class Row:
    label: str
    instance: str
    assigned: int
    total_jobs: int
    travel_s: int
    overtime_s: int
    sla_met: int
    wall_ms: int
    valid: bool
    proved_optimal: bool = False
    # True when CP-SAT found nothing in time and the greedy warm start was
    # returned instead. The row is a real answer to "solve this in 5s", but
    # it is not a search result and must not read as one.
    fell_back: bool = False

    @property
    def assigned_pct(self) -> float:
        return 100.0 * self.assigned / self.total_jobs if self.total_jobs else 0.0

    @property
    def travel_per_job_s(self) -> float:
        """The only travel figure comparable across strategies.

        Raw total travel is worthless when two strategies assign different
        numbers of jobs: a schedule that does 13 of 20 jobs will "win" on
        total travel against one that does 18, while being obviously worse.
        Per-assigned-job normalises that away.
        """
        return self.travel_s / self.assigned if self.assigned else float("inf")


@dataclass
class Report:
    rows: list[Row] = field(default_factory=list)
    reportable: bool = True
    matrix_source: str = "unknown"


def measure(
    problem: Problem, schedule: Schedule, label: str, instance: str, wall_ms: int
) -> Row:
    violations = check(problem, schedule)

    overtime = 0
    for tech_ref, visits in schedule.by_technician().items():
        tech = problem.tech(tech_ref)
        end = max((v.end_s for v in visits), default=tech.shift_start_s)
        overtime += max(0, end - tech.shift_end_s)

    # SLA here means the *preferred* window, the promise made to the customer.
    # The hard window is a constraint; meeting it is table stakes.
    sla_met = 0
    for v in schedule.visits:
        job = problem.job(v.job_ref)
        if job.pref_end_s is None or v.start_s <= job.pref_end_s:
            sla_met += 1

    return Row(
        label=label,
        instance=instance,
        assigned=len(schedule.visits),
        total_jobs=problem.n_jobs,
        travel_s=int(schedule.meta.get("travel_s", 0)),
        overtime_s=overtime,
        sla_met=sla_met,
        wall_ms=wall_ms,
        valid=not violations,
        proved_optimal=bool(schedule.meta.get("proved_optimal")),
        fell_back=bool(schedule.meta.get("fell_back")),
    )


async def build(
    n_jobs: int, n_techs: int, seed: int, provider, osrm_url: str | None = None
) -> Problem:
    """Generate an instance and attach a travel matrix.

    Coordinates are snapped to the road network first when OSRM is in play.
    Generated points are Gaussian around district centres, so a few land in
    parks and water; OSRM silently drags those to the nearest road on every
    request, which means the benchmark measures travel from somewhere the job
    is not. Snapping once here makes the point and its travel time agree.
    """
    inst = generate_instance(
        n_jobs=n_jobs, n_technicians=n_techs, day=DAY, seed=seed
    )
    if osrm_url and getattr(provider, "source", "") == "osrm":
        inst, _ = await snap_instance(inst, osrm_url)
    matrix = await provider.matrix(seed_coords(inst))
    return from_seed_instance(inst, matrix)


async def run(
    sizes: list[tuple[int, int]],
    seeds: list[int],
    limits: list[float],
    routing_mode: str,
    workers: int,
    reopt: bool,
) -> Report:
    provider = await build_provider(
        routing_mode, osrm_url=OSRM_URL,
        cache_path="/app/.cache/routing.json",
    )
    report = Report()

    for n_jobs, n_techs in sizes:
        for seed in seeds:
            problem = await build(n_jobs, n_techs, seed, provider, OSRM_URL)
            report.matrix_source = problem.travel.source
            report.reportable = report.reportable and problem.travel.is_reportable
            instance = f"{n_jobs}j/{n_techs}t s{seed}"

            for name, fn in BASELINES.items():
                t0 = time.perf_counter()
                sched = fn(problem)
                report.rows.append(
                    measure(
                        problem, sched, name, instance,
                        int((time.perf_counter() - t0) * 1000),
                    )
                )

            for limit in limits:
                cfg = SolverConfig(time_limit_s=limit, workers=workers)
                t0 = time.perf_counter()
                sched = solve(problem, cfg)
                report.rows.append(
                    measure(
                        problem, sched, f"solver {limit:g}s", instance,
                        int((time.perf_counter() - t0) * 1000),
                    )
                )

    if reopt:
        await _reopt_benchmark(sizes, seeds, provider, workers)

    return report


async def _reopt_benchmark(sizes, seeds, provider, workers) -> None:
    """Inject a midday disruption and compare warm re-optimisation against a
    naive full re-solve.

    The naive comparison is the point. A full re-solve will usually find a
    slightly cheaper schedule, because it is unconstrained by what was already
    promised. It also reshuffles the entire afternoon. The question is what
    that saving costs in customer calls.
    """
    print()
    print("=" * 78)
    print("RE-OPTIMISATION: midday disruption, warm re-solve vs naive full re-solve")
    print("=" * 78)
    print()
    print(f"{'instance':<16}{'':<10}{'travel':>9}{'moved':>7}{'calls':>7}"
          f"{'unassigned':>12}{'valid':>7}")
    print("-" * 78)

    for n_jobs, n_techs in sizes:
        for seed in seeds[:1]:
            problem = await build(n_jobs, n_techs, seed, provider, OSRM_URL)
            cfg = SolverConfig(time_limit_s=20, workers=workers)
            base = solve(problem, cfg)
            if not base.visits:
                continue

            now = 12 * 3600
            sick = sorted({v.technician_ref for v in base.visits})[0]
            disruption = Disruption(now_s=now, sick_technicians=frozenset({sick}))

            warm = reoptimise(problem, base, disruption, cfg)

            # Naive: same disruption, no pinning, no churn penalty. Free to
            # rewrite the whole day including work already done.
            naive_sched = solve(
                problem,
                SolverConfig(time_limit_s=20, workers=workers, w_churn=0),
                exclude_technicians={sick},
                not_before=now,
            )
            from solver.reoptimise import diff as _diff

            naive_moves = _diff(base, naive_sched)
            naive_calls = sum(
                1 for m in naive_moves if m.kind in ("reassigned", "dropped")
            )

            label = f"{n_jobs}j/{n_techs}t"
            print(
                f"{label:<16}{'warm':<10}"
                f"{int(warm.after.meta.get('travel_s', 0)) // 60:>8}m"
                f"{len(warm.moves):>7}{warm.churn:>7}"
                f"{len(warm.after.unassigned):>12}{str(warm.valid):>7}"
            )
            print(
                f"{'':<16}{'naive':<10}"
                f"{int(naive_sched.meta.get('travel_s', 0)) // 60:>8}m"
                f"{len(naive_moves):>7}{naive_calls:>7}"
                f"{len(naive_sched.unassigned):>12}"
                f"{str(not check(problem, naive_sched)):>7}"
            )
            print(
                f"{'':<16}{'-> pinning saves':<10} "
                f"{naive_calls - warm.churn} customer call(s)"
            )
            print()


def render(report: Report) -> str:
    out: list[str] = []
    add = out.append

    add("=" * 78)
    add("BENCHMARK")
    add("=" * 78)
    add("")
    add(f"travel matrix: {report.matrix_source}"
        + ("" if report.reportable else "   *** PROVISIONAL ***"))
    add("")
    add(f"{'instance':<16}{'strategy':<14}{'assigned':>10}{'travel':>9}"
        f"{'per job':>9}{'overtime':>10}{'SLA':>7}{'time':>9}{'valid':>7}")
    add("-" * 91)

    by_instance: dict[str, list[Row]] = {}
    for r in report.rows:
        by_instance.setdefault(r.instance, []).append(r)

    for instance in by_instance:
        for r in by_instance[instance]:
            opt = "*" if r.proved_optimal else ("+" if r.fell_back else "")
            add(
                f"{r.instance:<16}{r.label:<14}"
                f"{r.assigned:>4}/{r.total_jobs:<5}"
                f"{r.travel_s // 60:>8}m"
                f"{r.travel_per_job_s / 60:>8.1f}m"
                f"{r.overtime_s // 60:>9}m"
                f"{r.sla_met:>7}"
                f"{r.wall_ms:>8}ms"
                f"{('yes' if r.valid else 'NO'):>7}{opt}"
            )
        add("")

    # --- Aggregate improvement ---
    add("=" * 78)
    add("IMPROVEMENT vs baseline (averaged over instances, valid schedules only)")
    add("=" * 78)
    add("")

    def avg(label: str, field_name: str) -> float | None:
        """Average over valid runs, excluding any that found no solution.

        A run that assigned nothing has infinite travel-per-job. Averaging
        that in yields -inf% and destroys the table, so those runs are
        excluded from the means and reported separately instead -- silently
        dropping them would hide a real failure.
        """
        vals = [
            getattr(r, field_name)
            for r in report.rows
            if r.label == label and r.valid and r.assigned > 0
        ]
        return statistics.mean(vals) if vals else None

    labels = sorted({r.label for r in report.rows})
    solver_labels = [x for x in labels if x.startswith("solver")]

    empty = [r for r in report.rows if r.valid and r.assigned == 0]
    if empty:
        add("  NO SOLUTION FOUND within the time limit (excluded from means):")
        for r in empty:
            add(f"    {r.instance:<16} {r.label}")
        add("    -> the solver hit its limit before finding anything feasible.")
        add("       Raise the limit, or check the greedy warm start is active.")
        add("")

    fell = [r for r in report.rows if r.fell_back]
    if fell:
        add(f"  '+' {len(fell)} run(s) TIMED OUT and returned the greedy warm")
        add("      start unimproved. Those rows are greedy_nn's numbers under a")
        add("      solver label -- expect ~0% improvement there, and read it as")
        add("      'this size needs more than that limit', not as a tie:")
        for r in fell:
            add(f"    {r.instance:<16} {r.label}")
        add("")

    invalid = [r for r in report.rows if not r.valid]
    if invalid:
        add(f"  WARNING: {len(invalid)} run(s) produced an INVALID schedule "
            "and were excluded:")
        for r in invalid[:5]:
            add(f"    {r.instance} {r.label}")
        add("")

    add(f"{'':<16}{'travel':>12}{'per job':>12}{'assigned':>12}{'SLA met':>12}")
    add("-" * 64)
    for base_label in ("greedy_nn", "cluster_nn"):
        bt, ba, bs = (
            avg(base_label, "travel_s"),
            avg(base_label, "assigned"),
            avg(base_label, "sla_met"),
        )
        if bt is None:
            continue
        bp = avg(base_label, "travel_per_job_s")
        add(f"{base_label:<16}{bt / 60:>11.0f}m{bp / 60:>11.1f}m"
            f"{ba:>12.1f}{bs:>12.1f}")
    add("")

    for sl in solver_labels:
        st, sa, ss = (
            avg(sl, "travel_s"),
            avg(sl, "assigned"),
            avg(sl, "sla_met"),
        )
        if st is None:
            continue
        sp = avg(sl, "travel_per_job_s")
        add(f"{sl:<16}{st / 60:>11.0f}m{sp / 60:>11.1f}m"
            f"{sa:>12.1f}{ss:>12.1f}")
    add("")

    if not report.reportable:
        add("  NO HEADLINE FIGURE.")
        add("  These runs used the haversine fallback, not OSRM. Straight-line")
        add("  travel understates real Klang Valley driving badly and the")
        add("  numbers above are for development only. Start OSRM and re-run:")
        add("    docker compose --profile osrm up -d osrm")
        return "\n".join(out)

    # Which baseline is actually strongest is an empirical question, not an
    # assumption. Clustering was expected to beat pure greedy -- it often does
    # not, because carving the city into regions hurts when time windows are
    # tight. The headline compares against whichever baseline did best, so the
    # solver never gets credit for beating a strawman.
    baseline_labels = [x for x in ("greedy_nn", "cluster_nn")
                       if avg(x, "assigned") is not None]
    strongest = max(baseline_labels, key=lambda x: avg(x, "assigned"), default=None)
    if strongest:
        add(f"  strongest baseline on jobs done: {strongest}")
        add("")

    for base_label in baseline_labels:
        bt = avg(base_label, "travel_s")
        ba = avg(base_label, "assigned")
        if bt is None:
            continue
        bp = avg(base_label, "travel_per_job_s")
        marker = "  <- headline" if base_label == strongest else ""
        add(f"  vs {base_label}:{marker}")
        for sl in solver_labels:
            st, sa = avg(sl, "travel_s"), avg(sl, "assigned")
            sp = avg(sl, "travel_per_job_s")
            if st is None:
                continue
            job_gain = 100.0 * (sa - ba) / ba if ba else 0.0
            eff_gain = 100.0 * (bp - sp) / bp if bp else 0.0
            add(
                f"    {sl:<14} jobs done {job_gain:+6.1f}%   "
                f"travel per job {eff_gain:+6.1f}%"
            )
        add("")

    add("  How to read this:")
    add("    - POSITIVE IS BETTER IN BOTH COLUMNS. On travel per job that")
    add("      means a REDUCTION: '+17.1%' is 17.1% less driving per job,")
    add("      not more. Signs are aligned so a row is good or bad at a")
    add("      glance rather than needing a per-column convention.")
    add("    - Jobs done is the primary measure. The objective is built to")
    add("      never drop a job to save driving, so more jobs is the win.")
    add("    - Travel PER JOB is the only comparable travel figure. Raw")
    add("      totals are not: a strategy that does 13 of 20 jobs beats one")
    add("      that does 18 on total travel while being plainly worse.")
    add("")
    add("  Caveats, stated rather than buried:")
    add("    - OSRM's car profile is free-flow with no traffic model. These")
    add("      figures do not model a KL rush hour.")
    add("    - cluster_nn is the honest baseline. greedy_nn is weaker than a")
    add("      real dispatcher and the improvement against it looks better")
    add("      than it deserves to.")
    add("    - '*' marks runs where the solver proved optimality.")
    add("    - '+' marks runs that timed out and returned the greedy warm")
    add("      start unchanged -- greedy's result, not the solver's.")

    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(prog="python -m bench.harness")
    p.add_argument("--sizes", default="20x5,40x8,80x15",
                   help="comma-separated JOBSxTECHS")
    p.add_argument("--seeds", default="1,7,42")
    p.add_argument("--limits", default="5,30,120", help="solver seconds")
    p.add_argument("--routing", default="auto", choices=["auto", "osrm", "haversine"])
    # 4, not 8. CP-SAT holds a full copy of its search state per worker.
    # Measured on one 80x15 solve at 120s: 4 workers reached 70/80 at 747MB
    # peak RSS, 8 workers reached the same 70/80 at 1616MB. On a 7.6GB Docker
    # allocation shared with other stacks, 8 was enough to get every container
    # on the daemon OOM-killed partway through a full run.
    p.add_argument("--workers", type=int, default=4,
                   help="CP-SAT search workers; >1 is faster but "
                        "nondeterministic, and memory grows with it (see README)")
    p.add_argument("--reopt", action="store_true",
                   help="also run the re-optimisation benchmark")
    args = p.parse_args()

    sizes = []
    for chunk in args.sizes.split(","):
        j, t = chunk.lower().split("x")
        sizes.append((int(j), int(t)))
    seeds = [int(s) for s in args.seeds.split(",")]
    limits = [float(x) for x in args.limits.split(",")]

    report = asyncio.run(
        run(sizes, seeds, limits, args.routing, args.workers, args.reopt)
    )
    print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
