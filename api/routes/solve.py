"""Solve: trigger, poll status, fetch result, and drag-to-reassign."""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_settings
from api import actuals, repo, service
from api.db import get_session
from api.models import (
    ReoptimiseRequest,
    ReoptimiseResponse,
    ReassignPreview,
    ReassignRequest,
    SolveRequest,
    SolveResultOut,
    SolveRunOut,
)
from api.tables import JOB_STATUSES, SolveRun
from solver.model import Pin, SolverConfig, solve as run_solver
from solver.problem import hhmm_to_seconds
from solver.reoptimise import Disruption, diff, reoptimise

log = logging.getLogger("waypoint.routes.solve")
router = APIRouter(prefix="/solve", tags=["solve"])


def _config(req: SolveRequest) -> SolverConfig:
    return SolverConfig(
        time_limit_s=req.time_limit_s,
        workers=req.workers,
        allowed_overtime_s=req.allowed_overtime_minutes * 60,
        w_travel=req.w_travel,
        w_unassigned=req.w_unassigned,
        w_overtime=req.w_overtime,
        w_lateness=req.w_lateness,
        w_imbalance=req.w_imbalance,
    )


@router.post("", response_model=SolveRunOut, status_code=202)
async def trigger_solve(
    req: SolveRequest, session: AsyncSession = Depends(get_session)
) -> SolveRunOut:
    """Queue a solve on the Celery worker and return immediately.

    A solve takes seconds to minutes and must not block a request. The run row
    is created here so the caller has an id to poll before the worker has even
    picked the task up.
    """
    cfg = _config(req)
    run_id = await repo.create_run(session, req.day, cfg.as_dict(), status="queued")
    await session.commit()

    try:
        from worker.tasks import solve_day_task

        solve_day_task.delay(run_id, req.day.isoformat(), cfg.as_dict())
    except Exception as exc:  # noqa: BLE001
        # No broker, or the worker image is not running. Rather than fail the
        # request, fall back to solving inline -- slower, but the stack still
        # works for someone who only started the api and db.
        log.warning("could not queue to celery (%s); solving inline", exc)
        await service.solve_day(session, req.day, cfg, run_id=run_id)

    row = await session.get(SolveRun, run_id)
    return SolveRunOut.model_validate(row, from_attributes=True)


@router.post("/sync", response_model=SolveResultOut)
async def solve_now(
    req: SolveRequest, session: AsyncSession = Depends(get_session)
) -> SolveResultOut:
    """Solve inline and return the full result. Convenient for the CLI and
    tests; the UI uses the queued path."""
    try:
        return await service.solve_day(session, req.day, _config(req))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/runs", response_model=list[SolveRunOut])
async def list_runs(
    day: date | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[SolveRunOut]:
    stmt = select(SolveRun).order_by(SolveRun.created_at.desc()).limit(limit)
    if day:
        stmt = stmt.where(SolveRun.day == day)
    rows = (await session.execute(stmt)).scalars().all()
    return [SolveRunOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/runs/{run_id}", response_model=SolveRunOut)
async def get_run(
    run_id: int, session: AsyncSession = Depends(get_session)
) -> SolveRunOut:
    row = await session.get(SolveRun, run_id)
    if row is None:
        raise HTTPException(404, f"no run {run_id}")
    return SolveRunOut.model_validate(row, from_attributes=True)


@router.get("/runs/{run_id}/result", response_model=SolveResultOut)
async def get_run_result(
    run_id: int, session: AsyncSession = Depends(get_session)
) -> SolveResultOut:
    row = await session.get(SolveRun, run_id)
    if row is None:
        raise HTTPException(404, f"no run {run_id}")
    if row.status not in ("succeeded", "failed"):
        raise HTTPException(409, f"run {run_id} is {row.status}")

    problem, tech_ids, job_ids = await service.build_problem_for(
        session, row.day, include_statuses=JOB_STATUSES
    )
    schedule = await repo.load_schedule(session, run_id, problem)

    from solver.explain import explain_schedule

    explanations = (
        explain_schedule(problem, schedule, probe=False)
        if schedule.unassigned
        else []
    )
    return service.to_result(
        problem, schedule, tech_ids, job_ids, run_id, explanations
    )


@router.get("/day/{day}/latest", response_model=SolveResultOut)
async def latest_for_day(
    day: date, session: AsyncSession = Depends(get_session)
) -> SolveResultOut:
    run_id = await repo.latest_run_id(session, day)
    if run_id is None:
        raise HTTPException(404, f"no successful run for {day}")
    return await get_run_result(run_id, session)


@router.post("/reassign", response_model=ReassignPreview)
async def reassign(
    req: ReassignRequest, session: AsyncSession = Depends(get_session)
) -> ReassignPreview:
    """Drag-to-reassign: pin one job to one technician and re-solve.

    Always a preview by default. `commit=true` writes the result as a new run.
    An impossible move -- the technician lacks the skill, or cannot reach it --
    comes back as ok=false with the reason, not as an error, because the UI
    needs to show the dispatcher why the drag was refused.
    """
    row = await session.get(SolveRun, req.run_id)
    if row is None:
        raise HTTPException(404, f"no run {req.run_id}")

    problem, tech_ids, job_ids = await service.build_problem_for(
        session, row.day, include_statuses=JOB_STATUSES
    )
    before = await repo.load_schedule(session, req.run_id, problem)

    job_ref = f"J{req.job_id}"
    tech_ref = f"T{req.technician_id}"
    try:
        job = problem.job(job_ref)
        tech = problem.tech(tech_ref)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

    # Cheap refusals first, with a reason a dispatcher can act on.
    missing_skills = sorted(job.skills - tech.skills)
    if missing_skills:
        return ReassignPreview(
            ok=False,
            reason=f"{tech.name} does not have {', '.join(missing_skills)}",
            travel_delta_minutes=0,
            unassigned_delta=0,
            moved_jobs=[],
            customer_calls=0,
            valid=True,
        )
    missing_parts = sorted(p for p in job.parts if p not in tech.van_stock)
    if missing_parts:
        return ReassignPreview(
            ok=False,
            reason=f"{tech.name}'s van does not carry {', '.join(missing_parts)}",
            travel_delta_minutes=0,
            unassigned_delta=0,
            moved_jobs=[],
            customer_calls=0,
            valid=True,
        )
    if not problem.reachable(tech, job):
        return ReassignPreview(
            ok=False,
            reason=(
                f"{tech.name} cannot reach {job.name} inside its time window "
                "even on an empty day"
            ),
            travel_delta_minutes=0,
            unassigned_delta=0,
            moved_jobs=[],
            customer_calls=0,
            valid=True,
        )

    cfg = SolverConfig(
        time_limit_s=req.time_limit_s,
        workers=get_settings().solver_workers,
        w_churn=900,
    )
    previous = {v.job_ref: v.technician_ref for v in before.visits}
    after = run_solver(
        problem,
        cfg,
        pins=[Pin(job_ref, tech_ref)],
        previous=previous,
        require=[job_ref],
        hint=before,
    )

    if job_ref in after.unassigned or not after.visits:
        return ReassignPreview(
            ok=False,
            reason=(
                f"no valid schedule exists with {job.name} on {tech.name} "
                "alongside the rest of the day"
            ),
            travel_delta_minutes=0,
            unassigned_delta=0,
            moved_jobs=[],
            customer_calls=0,
            valid=True,
        )

    from solver.check import check

    violations = check(problem, after)
    moves = diff(before, after)
    call_moves = [m for m in moves if m.kind in ("reassigned", "dropped")]
    calls = len(call_moves)

    # Name the customers rather than just counting them. A retimed job stays
    # with the same technician inside the window that was promised, so it is
    # not a call; a reassignment or a drop is.
    names = {j.ref: j.name for j in problem.jobs}
    tech_names = {t.ref: t.name for t in problem.technicians}
    call_lines = []
    for m in call_moves:
        who = names.get(m.job_ref, m.job_ref)
        if m.kind == "dropped":
            call_lines.append(f"{who}: dropped from today's schedule")
        else:
            call_lines.append(
                f"{who}: {tech_names.get(m.from_technician, m.from_technician)}"
                f" -> {tech_names.get(m.to_technician, m.to_technician)}"
            )

    new_run_id = req.run_id
    if req.commit and not violations:
        new_run_id = await repo.create_run(
            session, row.day, {**cfg.as_dict(), "origin": "reassign"}, status="running"
        )
        await repo.store_result(
            session, new_run_id, problem, after, tech_ids, job_ids, valid=True
        )
        await session.commit()

    return ReassignPreview(
        ok=True,
        travel_delta_minutes=(
            int(after.meta.get("travel_s", 0)) - int(before.meta.get("travel_s", 0))
        )
        // 60,
        unassigned_delta=len(after.unassigned) - len(before.unassigned),
        moved_jobs=[str(m) for m in moves],
        customer_calls=calls,
        calls=call_lines,
        valid=not violations,
        result=service.to_result(
            problem, after, tech_ids, job_ids, new_run_id if req.commit else None
        ),
    )


@router.post("/reoptimise", response_model=ReoptimiseResponse)
async def reoptimise_now(
    req: ReoptimiseRequest, session: AsyncSession = Depends(get_session)
) -> ReoptimiseResponse:
    """Re-plan the rest of the day around what has actually happened.

    No disruption, no dispatcher instruction -- just "here is the time, here
    is what the technicians have reported, work out the rest". This is the
    route that makes the field app's whole purpose observable: complete a job
    late, call this, and the afternoon moves.

    Previews by default. `commit=true` stores the result as a new run, which
    makes it the schedule `/field/today` serves and fires the phase 8 change
    notices -- so a re-plan reaches the technicians it affects the same way a
    dispatcher's reassignment does.
    """
    row = await session.get(SolveRun, req.run_id)
    if row is None:
        raise HTTPException(404, f"no run {req.run_id}")

    problem, tech_ids, job_ids = await service.build_problem_for(
        session, row.day, include_statuses=JOB_STATUSES
    )
    current = await repo.load_schedule(session, req.run_id, problem)
    reported = await actuals.load_actuals(
        session, run_id=req.run_id, day=row.day, timezone=get_settings().timezone
    )

    result = reoptimise(
        problem,
        current,
        Disruption(now_s=hhmm_to_seconds(req.now), actuals=reported),
        SolverConfig(time_limit_s=req.time_limit_s, workers=req.workers),
    )

    run_id: int | None = None
    if req.commit and result.after.visits and result.valid:
        run_id = await repo.create_run(
            session,
            row.day,
            {"origin": "reoptimise", "from_run": req.run_id},
            status="running",
        )
        await repo.store_result(
            session, run_id, problem, result.after, tech_ids, job_ids,
            valid=result.valid,
        )
        await session.commit()

    # An empty schedule is not a plan in which everything was dropped, it is
    # the solver saying it could not build a model. Reported as ok=False so a
    # caller cannot mistake one for the other -- "38 jobs moved, 38 customer
    # calls" reads like a decision, and it was a failure.
    produced = bool(result.after.visits)

    return ReoptimiseResponse(
        ok=produced and result.valid,
        solver_status=str(result.after.meta.get("status", "unknown")),
        run_id=run_id,
        reported=len(reported),
        untrusted=sum(1 for a in reported if not a.trusted),
        drift_minutes={tech: secs // 60 for tech, secs in result.drift.items()},
        moves=[str(m) for m in result.moves],
        travel_delta_minutes=result.travel_delta_s // 60,
        unassigned_delta=result.unassigned_delta,
        customer_calls=result.churn,
        valid=result.valid,
        summary=result.summary(),
    )
