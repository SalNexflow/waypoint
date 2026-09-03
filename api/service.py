"""The solve/re-solve service. Shared by the API routes and the Celery worker.

One place where "load a day, get a matrix, solve it, check it, explain it,
store it" is written down.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from api import repo
from api.config import get_settings
from api.models import (
    RouteOut,
    SolveMetrics,
    SolveResultOut,
    UnassignedOut,
    VisitOut,
)
from routing import build_provider
from solver.check import check
from solver.explain import explain_schedule
from solver.model import Pin, SolverConfig, solve
from solver.problem import Problem, hhmm
from solver.solution import Schedule

log = logging.getLogger("waypoint.service")


async def build_problem_for(
    session: AsyncSession,
    day: date,
    *,
    include_statuses: tuple[str, ...] = repo.SOLVABLE_STATUSES,
) -> tuple[Problem, dict[str, int], dict[str, int]]:
    """Assemble a Problem for a day.

    `include_statuses` decides which jobs exist in the model, and the two
    callers want different answers:

    * **Solving a day fresh** wants only unfinished work. Re-scheduling a job
      a technician already completed is exactly the bug status reporting
      exists to prevent -- hence the default.
    * **Reconstructing an EXISTING run** wants every job that was in it,
      finished or not. Pass `JOB_STATUSES`.

    That second case is not a nicety. `repo.load_schedule` skips assignments
    whose job is absent from the Problem, so a run rebuilt without its
    completed jobs comes back with GAPS in each technician's sequence -- the
    dispatcher console shows a route with holes, and the checker rejects the
    schedule as invalid. Nothing surfaced this until the field app became the
    first thing in the system that ever sets a job to `done`.

    Re-optimisation needs them for a further reason of its own: a completed
    job is what tells the solver where a technician physically is and when
    they became free (solver/reoptimise.py). Drop it and the model thinks
    they are still at home.
    """
    settings = get_settings()
    technicians, jobs, coords, tech_ids, job_ids = await repo.load_day(
        session, day, settings.timezone, include_statuses=include_statuses
    )
    if not technicians:
        raise ValueError("no technicians in the database -- seed some first")
    if not jobs:
        raise ValueError(f"no jobs on {day} -- seed some first")

    provider = await build_provider(
        settings.routing_provider,
        osrm_url=settings.osrm_url,
        cache_path=settings.routing_cache_path,
        frozen_path=settings.frozen_matrix_path,
        speed_kmh=settings.haversine_speed_kmh,
        detour_factor=settings.haversine_detour_factor,
    )
    matrix = await provider.matrix(coords)
    problem = repo.assemble(day, settings.timezone, technicians, jobs, matrix)
    return problem, tech_ids, job_ids


def to_result(
    problem: Problem,
    schedule: Schedule,
    tech_ids: dict[str, int],
    job_ids: dict[str, int],
    run_id: int | None,
    explanations=None,
) -> SolveResultOut:
    violations = check(problem, schedule)

    routes: list[RouteOut] = []
    for tech in problem.technicians:
        visits = schedule.by_technician().get(tech.ref, [])
        node = tech.node
        clock = tech.shift_start_s
        travel = work = wait = 0
        out_visits: list[VisitOut] = []
        for v in visits:
            job = problem.job(v.job_ref)
            travel += problem.travel_s(node, job.node)
            work += job.duration_s
            wait += v.wait_s
            out_visits.append(
                VisitOut(
                    job_id=job_ids.get(v.job_ref, 0),
                    job_ref=v.job_ref,
                    customer=job.name,
                    technician_id=tech_ids.get(tech.ref, 0),
                    technician_ref=tech.ref,
                    technician_name=tech.name,
                    sequence=v.sequence,
                    arrive=hhmm(v.arrive_s),
                    start=hhmm(v.start_s),
                    end=hhmm(v.end_s),
                    wait_minutes=v.wait_s // 60,
                    lat=job.lat,
                    lon=job.lon,
                )
            )
            node = job.node
            clock = v.end_s

        routes.append(
            RouteOut(
                technician_id=tech_ids.get(tech.ref, 0),
                technician_ref=tech.ref,
                technician_name=tech.name,
                shift_start=hhmm(tech.shift_start_s),
                shift_end=hhmm(tech.shift_end_s),
                home_lat=tech.lat,
                home_lon=tech.lon,
                visits=out_visits,
                travel_minutes=travel // 60,
                work_minutes=work // 60,
                wait_minutes=wait // 60,
            )
        )

    by_ref = {e.job_ref: e for e in (explanations or [])}
    unassigned: list[UnassignedOut] = []
    for ref in schedule.unassigned:
        job = problem.job(ref)
        e = by_ref.get(ref)
        unassigned.append(
            UnassignedOut(
                job_id=job_ids.get(ref, 0),
                job_ref=ref,
                customer=job.name,
                lat=job.lat,
                lon=job.lon,
                reason=e.reason.value if e else "undetermined",
                message=e.message if e else "not explained",
            )
        )

    meta = schedule.meta
    return SolveResultOut(
        run_id=run_id,
        day=problem.day,
        metrics=SolveMetrics(
            status=str(meta.get("status", "UNKNOWN")),
            proved_optimal=bool(meta.get("proved_optimal")),
            fell_back=bool(meta.get("fell_back")),
            objective_value=meta.get("objective"),
            travel_minutes=int(meta.get("travel_s", 0)) // 60,
            assigned=len(schedule.visits),
            total_jobs=problem.n_jobs,
            unassigned_count=len(schedule.unassigned),
            solver_wall_ms=int(meta.get("wall_ms", 0)),
            matrix_source=str(meta.get("matrix_source", "unknown")),
            reportable=bool(meta.get("reportable")),
            valid=not violations,
            violations=[str(v) for v in violations],
        ),
        routes=routes,
        unassigned=unassigned,
    )


async def solve_day(
    session: AsyncSession,
    day: date,
    config: SolverConfig,
    *,
    run_id: int | None = None,
    explain: bool = True,
    store: bool = True,
) -> SolveResultOut:
    problem, tech_ids, job_ids = await build_problem_for(session, day)

    if store and run_id is None:
        run_id = await repo.create_run(session, day, config.as_dict(), status="running")
        await session.commit()

    schedule = solve(problem, config)
    violations = check(problem, schedule, allowed_overtime_s=config.allowed_overtime_s)
    if violations:
        log.error(
            "INVALID schedule from solver on %s: %s",
            day,
            "; ".join(str(v) for v in violations[:3]),
        )

    explanations = []
    if explain and schedule.unassigned:
        # probe=False keeps the API responsive; the explainer's re-solves are
        # the expensive part and the static reasons cover most cases.
        explanations = explain_schedule(problem, schedule, probe=False)

    if store and run_id is not None:
        await repo.store_result(
            session,
            run_id,
            problem,
            schedule,
            tech_ids,
            job_ids,
            valid=not violations,
        )
        await session.commit()

    return to_result(problem, schedule, tech_ids, job_ids, run_id, explanations)
