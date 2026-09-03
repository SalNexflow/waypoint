"""Celery tasks: solve_day and reoptimise_day.

Celery tasks are synchronous functions, but everything underneath is async, so
each task opens its own event loop with asyncio.run(). That is safe here
because a Celery worker process handles one task at a time (prefetch is 1) and
there is no ambient loop to conflict with.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from celery import shared_task

log = logging.getLogger("waypoint.worker")


@shared_task(name="waypoint.solve_day", bind=True)
def solve_day_task(self, run_id: int, day_iso: str, config: dict) -> dict:
    return asyncio.run(_solve_day(run_id, date.fromisoformat(day_iso), config))


async def _solve_day(run_id: int, day: date, config: dict) -> dict:
    from api import service
    from api.db import SessionFactory
    from api.tables import JOB_STATUSES, SolveRun
    from solver.model import SolverConfig

    cfg = SolverConfig(
        time_limit_s=float(config.get("time_limit_s", 30.0)),
        workers=int(config.get("workers", 8)),
        allowed_overtime_s=int(config.get("allowed_overtime_s", 0)),
        w_travel=int(config.get("w_travel", 1)),
        w_unassigned=int(config.get("w_unassigned", 1_000_000)),
        w_overtime=int(config.get("w_overtime", 20)),
        w_lateness=int(config.get("w_lateness", 3)),
        w_imbalance=int(config.get("w_imbalance", 0)),
    )

    async with SessionFactory() as session:
        run = await session.get(SolveRun, run_id)
        if run is not None:
            run.status = "running"
            await session.commit()

        try:
            result = await service.solve_day(session, day, cfg, run_id=run_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("solve failed for run %s", run_id)
            async with SessionFactory() as s2:
                r = await s2.get(SolveRun, run_id)
                if r is not None:
                    r.status = "failed"
                    r.error = str(exc)[:1000]
                    await s2.commit()
            raise

        return {
            "run_id": run_id,
            "assigned": result.metrics.assigned,
            "unassigned": result.metrics.unassigned_count,
            "travel_minutes": result.metrics.travel_minutes,
            "valid": result.metrics.valid,
        }


@shared_task(name="waypoint.reoptimise_day", bind=True)
def reoptimise_day_task(
    self, run_id: int, day_iso: str, now_s: int, sick: list[str], config: dict
) -> dict:
    return asyncio.run(
        _reoptimise(run_id, date.fromisoformat(day_iso), now_s, sick, config)
    )


async def _reoptimise(
    run_id: int, day: date, now_s: int, sick: list[str], config: dict
) -> dict:
    from api import actuals as actuals_repo
    from api import repo, service
    from api.config import get_settings
    from api.db import SessionFactory
    from api.tables import JOB_STATUSES
    from solver.model import SolverConfig
    from solver.reoptimise import Disruption, reoptimise

    cfg = SolverConfig(
        time_limit_s=float(config.get("time_limit_s", 30.0)),
        workers=int(config.get("workers", 8)),
    )

    async with SessionFactory() as session:
        problem, tech_ids, job_ids = await service.build_problem_for(
            session, day, include_statuses=JOB_STATUSES
        )
        current = await repo.load_schedule(session, run_id, problem)

        # What the technicians actually reported. Without this the re-solve
        # assumes every job finished exactly when the morning predicted, and
        # a technician forty minutes behind is re-planned as though on time.
        reported = await actuals_repo.load_actuals(
            session,
            run_id=run_id,
            day=day,
            timezone=get_settings().timezone,
        )

        result = reoptimise(
            problem,
            current,
            Disruption(
                now_s=now_s,
                sick_technicians=frozenset(sick),
                actuals=reported,
            ),
            cfg,
        )
        new_run = await repo.create_run(
            session, day, {**cfg.as_dict(), "origin": "reoptimise"}, status="running"
        )
        await repo.store_result(
            session,
            new_run,
            problem,
            result.after,
            tech_ids,
            job_ids,
            valid=result.valid,
        )
        await session.commit()

    return {
        "run_id": new_run,
        "moves": len(result.moves),
        "customer_calls": result.churn,
        "travel_delta_minutes": result.travel_delta_s // 60,
        "valid": result.valid,
    }
