"""Orchestration: load a day, solve it, check it, explain it.

The layer between the pure solver and everything that has side effects. The
API and the Celery worker both come through here, so the "solve a day"
sequence exists once.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from routing import build_provider
from routing.base import TravelTimeProvider
from solver.check import Violation, check
from solver.explain import Explanation, explain_schedule
from solver.model import Pin, SolverConfig, solve
from solver.problem import Problem, from_seed_instance, seed_coords
from solver.solution import Schedule

log = logging.getLogger("waypoint.solver.run")


@dataclass(frozen=True)
class SolveResult:
    problem: Problem
    schedule: Schedule
    violations: list[Violation]
    explanations: list[Explanation]

    @property
    def valid(self) -> bool:
        return not self.violations

    @property
    def travel_s(self) -> int:
        return int(self.schedule.meta.get("travel_s", 0))

    @property
    def assigned(self) -> int:
        return len(self.schedule.visits)

    def metrics(self) -> dict:
        """The row that goes into solve_runs."""
        m = self.schedule.meta
        return {
            "status": m.get("status"),
            "proved_optimal": bool(m.get("proved_optimal")),
            "objective_value": m.get("objective"),
            "travel_seconds_total": self.travel_s,
            "unassigned_count": len(self.schedule.unassigned),
            "solver_wall_ms": m.get("wall_ms"),
            "matrix_source": m.get("matrix_source"),
            "reportable": m.get("reportable"),
            "valid": self.valid,
            "violation_count": len(self.violations),
        }


async def solve_problem(
    problem: Problem,
    config: SolverConfig | None = None,
    *,
    pins: list[Pin] | None = None,
    previous: dict[str, str] | None = None,
    explain: bool = True,
    probe: bool = True,
) -> SolveResult:
    """Solve, then independently verify, then explain what did not fit.

    The check always runs. It is cheap relative to the solve, and the whole
    point of phase 5 is that solver output is not trusted on its own -- so
    running the checker only in tests would defeat it.
    """
    cfg = config or SolverConfig()
    schedule = solve(problem, cfg, pins=pins, previous=previous)

    violations = check(problem, schedule, allowed_overtime_s=cfg.allowed_overtime_s)
    if violations:
        # Loud, because this means the model and reality disagree.
        log.error(
            "solver returned an INVALID schedule with %d violation(s): %s",
            len(violations),
            "; ".join(str(v) for v in violations[:3]),
        )

    explanations: list[Explanation] = []
    if explain and schedule.unassigned:
        explanations = explain_schedule(
            problem, schedule, probe=probe, config=cfg
        )

    return SolveResult(problem, schedule, violations, explanations)


async def solve_seed_day(
    *,
    n_jobs: int = 40,
    n_technicians: int = 8,
    seed: int = 42,
    day: date | None = None,
    config: SolverConfig | None = None,
    routing_mode: str = "auto",
    osrm_url: str = "http://osrm:5000",
    cache_path: str | None = "/app/.cache/routing.json",
    explain: bool = True,
) -> SolveResult:
    """Generate a day, fetch its matrix, and solve it. No database involved."""
    from data.seed.generate import generate_instance

    inst = generate_instance(
        n_jobs=n_jobs, n_technicians=n_technicians, day=day, seed=seed
    )
    provider = await build_provider(
        routing_mode, osrm_url=osrm_url, cache_path=cache_path
    )
    matrix = await provider.matrix(seed_coords(inst))
    problem = from_seed_instance(inst, matrix)
    return await solve_problem(problem, config, explain=explain)


async def matrix_for(
    problem_coords, mode: str = "auto", osrm_url: str = "http://osrm:5000"
):
    provider: TravelTimeProvider = await build_provider(mode, osrm_url=osrm_url)
    return await provider.matrix(problem_coords)


if __name__ == "__main__":
    import argparse
    import asyncio

    from solver.check import summarise as summarise_violations
    from solver.explain import summarise as summarise_explanations
    from solver.solution import render

    p = argparse.ArgumentParser(prog="python -m solver.run")
    p.add_argument("--jobs", type=int, default=40)
    p.add_argument("--technicians", type=int, default=8)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--limit", type=float, default=30.0, help="solver seconds")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--overtime", type=int, default=0, help="allowed overtime minutes")
    p.add_argument("--imbalance", type=int, default=0, help="imbalance weight")
    p.add_argument("--quiet", action="store_true", help="metrics only")
    p.add_argument("--no-probe", action="store_true", help="skip explainer re-solves")
    args = p.parse_args()

    async def main() -> int:
        cfg = SolverConfig(
            time_limit_s=args.limit,
            workers=args.workers,
            allowed_overtime_s=args.overtime * 60,
            w_imbalance=args.imbalance,
        )
        result = await solve_seed_day(
            n_jobs=args.jobs,
            n_technicians=args.technicians,
            seed=args.seed,
            config=cfg,
        )
        if not args.quiet:
            print(render(result.problem, result.schedule))
            print()
        print(summarise_violations(result.violations))
        print()
        if result.schedule.unassigned:
            print(summarise_explanations(result.explanations))
            print()
        for k, v in result.metrics().items():
            print(f"  {k:<22} {v}")
        return 0 if result.valid else 1

    raise SystemExit(asyncio.run(main()))
