"""Database <-> solver translation.

Loads a day out of Postgres into a Problem, and writes a Schedule back as a
solve_run plus assignments. Kept apart from the routes so the Celery worker
can use exactly the same code without importing FastAPI.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from geoalchemy2.shape import to_shape
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api import schedule_changes
from api.config import get_settings
from api.tables import Assignment, Job, SolveRun, Technician
from routing.base import Coord, TravelMatrix
from solver.problem import (
    Problem,
    ProblemJob,
    ProblemTech,
    build_problem,
    datetime_to_seconds,
    seconds_since_midnight,
)
from solver.solution import Schedule, Visit


def _point(col) -> tuple[float, float]:
    """PostGIS geography -> (lat, lon).

    to_shape gives a shapely Point in (x, y) = (lon, lat) order. Flipped here,
    once, so nothing downstream has to remember.
    """
    p = to_shape(col)
    return p.y, p.x


# Which jobs a FRESH solve should consider. `done` and `cancelled` are
# excluded: re-scheduling finished work is the bug that status reporting
# exists to prevent. Rebuilding an existing run is the opposite case and
# passes JOB_STATUSES instead -- see service.build_problem_for.
SOLVABLE_STATUSES: tuple[str, ...] = ("pending", "assigned", "in_progress")


async def load_day(
    session: AsyncSession,
    day: date,
    timezone: str,
    matrix: TravelMatrix | None = None,
    *,
    include_statuses: tuple[str, ...] = SOLVABLE_STATUSES,
) -> tuple[list[ProblemTech], list[ProblemJob], list[Coord], dict, dict]:
    """Read technicians and the day's jobs, in node order.

    Returns the pieces rather than a Problem, because the caller needs the
    coordinate list to fetch a travel matrix before a Problem can exist.
    Also returns ref->id maps in both directions; the solver works in refs
    ("T3", "J17") and the database in integer ids.
    """
    tz = ZoneInfo(timezone)

    tech_rows = (
        (await session.execute(select(Technician).order_by(Technician.id)))
        .scalars()
        .all()
    )
    day_start = datetime.combine(day, time(0, 0), tzinfo=tz)
    day_end = day_start + timedelta(days=1)

    job_rows = (
        (
            await session.execute(
                select(Job)
                .where(Job.hard_window_start >= day_start)
                .where(Job.hard_window_start < day_end)
                .where(Job.status.in_(include_statuses))
                .order_by(Job.id)
            )
        )
        .scalars()
        .all()
    )

    technicians: list[ProblemTech] = []
    tech_ids: dict[str, int] = {}
    for i, row in enumerate(tech_rows):
        lat, lon = _point(row.home_location)
        ref = f"T{row.id}"
        tech_ids[ref] = row.id
        technicians.append(
            ProblemTech(
                ref=ref,
                name=row.name,
                node=i,
                skills=frozenset(row.skills or []),
                van_stock=dict(row.van_stock or {}),
                shift_start_s=seconds_since_midnight(row.shift_start),
                shift_end_s=seconds_since_midnight(row.shift_end),
                max_jobs=row.max_jobs,
                lat=lat,
                lon=lon,
            )
        )

    jobs: list[ProblemJob] = []
    job_ids: dict[str, int] = {}
    offset = len(technicians)
    for i, row in enumerate(job_rows):
        lat, lon = _point(row.location)
        ref = f"J{row.id}"
        job_ids[ref] = row.id
        jobs.append(
            ProblemJob(
                ref=ref,
                name=row.customer,
                node=offset + i,
                duration_s=row.duration_seconds,
                skills=frozenset(row.required_skills or []),
                parts=frozenset(row.required_parts or []),
                hard_start_s=datetime_to_seconds(row.hard_window_start, day, tz),
                hard_end_s=datetime_to_seconds(row.hard_window_end, day, tz),
                pref_start_s=(
                    datetime_to_seconds(row.pref_window_start, day, tz)
                    if row.pref_window_start
                    else None
                ),
                pref_end_s=(
                    datetime_to_seconds(row.pref_window_end, day, tz)
                    if row.pref_window_end
                    else None
                ),
                priority=row.priority,
                lat=lat,
                lon=lon,
            )
        )

    coords = [Coord(t.lat, t.lon) for t in technicians] + [
        Coord(j.lat, j.lon) for j in jobs
    ]
    return technicians, jobs, coords, tech_ids, job_ids


def assemble(
    day: date,
    timezone: str,
    technicians: list[ProblemTech],
    jobs: list[ProblemJob],
    matrix: TravelMatrix,
) -> Problem:
    return build_problem(
        day=day,
        timezone=timezone,
        technicians=technicians,
        jobs=jobs,
        travel=matrix,
    )


async def create_run(
    session: AsyncSession, day: date, config: dict, status: str = "queued"
) -> int:
    run = SolveRun(day=day, status=status, config_snapshot=config)
    session.add(run)
    await session.flush()
    return run.id


async def store_result(
    session: AsyncSession,
    run_id: int,
    problem: Problem,
    schedule: Schedule,
    tech_ids: dict[str, int],
    job_ids: dict[str, int],
    *,
    valid: bool,
    error: str | None = None,
) -> None:
    """Write the schedule and its metrics, replacing anything already stored
    for this run."""
    tz = ZoneInfo(problem.timezone)
    midnight = datetime.combine(problem.day, time(0, 0), tzinfo=tz)

    # Captured BEFORE this run's assignments land, because "the run a
    # technician would have been looking at" is a question about the state
    # before this write, and once this run is marked succeeded it becomes the
    # newest one itself.
    previous_run = await schedule_changes.previous_run_id(session, problem.day, run_id)

    await session.execute(delete(Assignment).where(Assignment.solve_run_id == run_id))

    for v in schedule.visits:
        session.add(
            Assignment(
                solve_run_id=run_id,
                job_id=job_ids[v.job_ref],
                technician_id=tech_ids[v.technician_ref],
                sequence_position=v.sequence,
                predicted_arrival=midnight + timedelta(seconds=v.arrive_s),
                predicted_departure=midnight + timedelta(seconds=v.end_s),
            )
        )

    run = await session.get(SolveRun, run_id)
    if run is None:
        return
    meta = schedule.meta
    run.status = "failed" if error else "succeeded"
    run.objective_value = meta.get("objective")
    run.travel_seconds_total = int(meta.get("travel_s", 0))
    run.unassigned_count = len(schedule.unassigned)
    run.solver_wall_ms = int(meta.get("wall_ms", 0))
    run.proved_optimal = bool(meta.get("proved_optimal"))
    run.error = error
    snapshot = dict(run.config_snapshot or {})
    snapshot.update(
        {
            "solver_status": meta.get("status"),
            "matrix_source": meta.get("matrix_source"),
            "reportable": meta.get("reportable"),
            "checker_valid": valid,
        }
    )
    run.config_snapshot = snapshot

    # Tell whoever this moved.
    #
    # Here rather than in a route because store_result is the ONLY place
    # assignments are written -- a plain solve, a drag-to-reassign, a typed
    # dispatch instruction and the Celery re-optimisation all pass through it.
    # Detection wired in anywhere else would have covered some of them and
    # quietly missed the rest.
    #
    # Not for a failed run: nothing was scheduled, so nothing changed.
    if error is None and previous_run is not None:
        await session.flush()  # assignments visible to the comparison below
        await schedule_changes.detect(
            session,
            day=problem.day,
            run_id=run_id,
            retime_threshold_seconds=get_settings().schedule_change_retime_minutes * 60,
        )


async def load_schedule(
    session: AsyncSession, run_id: int, problem: Problem
) -> Schedule:
    """Rebuild a Schedule from stored assignments.

    Needed by re-optimisation and drag-to-reassign, which operate on a run
    that a previous request produced.
    """
    tz = ZoneInfo(problem.timezone)
    midnight = datetime.combine(problem.day, time(0, 0), tzinfo=tz)

    rows = (
        (
            await session.execute(
                select(Assignment, Job.id, Technician.id)
                .join(Job, Assignment.job_id == Job.id)
                .join(Technician, Assignment.technician_id == Technician.id)
                .where(Assignment.solve_run_id == run_id)
                .order_by(Assignment.technician_id, Assignment.sequence_position)
            )
        )
        .all()
    )

    visits: list[Visit] = []
    assigned: set[str] = set()
    for a, job_id, tech_id in rows:
        job_ref = f"J{job_id}"
        try:
            job = problem.job(job_ref)
        except KeyError:
            continue
        arrive = int((a.predicted_arrival.astimezone(tz) - midnight).total_seconds())
        end = int((a.predicted_departure.astimezone(tz) - midnight).total_seconds())
        visits.append(
            Visit(
                job_ref=job_ref,
                technician_ref=f"T{tech_id}",
                sequence=a.sequence_position,
                arrive_s=arrive,
                start_s=end - job.duration_s,
                end_s=end,
            )
        )
        assigned.add(job_ref)

    run = await session.get(SolveRun, run_id)
    travel = 0
    by_tech: dict[str, list[Visit]] = {}
    for v in visits:
        by_tech.setdefault(v.technician_ref, []).append(v)
    for tech_ref, route in by_tech.items():
        route.sort(key=lambda v: v.sequence)
        node = problem.tech(tech_ref).node
        for v in route:
            j = problem.job(v.job_ref)
            travel += problem.travel_s(node, j.node)
            node = j.node

    return Schedule(
        day=problem.day,
        visits=tuple(visits),
        unassigned=tuple(sorted(j.ref for j in problem.jobs if j.ref not in assigned)),
        meta={
            "status": "LOADED",
            "travel_s": travel,
            "objective": run.objective_value if run else None,
            "proved_optimal": bool(run.proved_optimal) if run else False,
            "matrix_source": problem.travel.source,
            "reportable": problem.travel.is_reportable,
            "wall_ms": run.solver_wall_ms if run else 0,
        },
    )


async def latest_run_id(session: AsyncSession, day: date) -> int | None:
    return (
        await session.execute(
            select(SolveRun.id)
            .where(SolveRun.day == day)
            .where(SolveRun.status == "succeeded")
            .order_by(SolveRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
