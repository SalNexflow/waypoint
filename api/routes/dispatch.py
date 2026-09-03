"""Natural-language dispatch: parse, preview, confirm.

Two endpoints deliberately, not one. `/parse` turns text into a typed change
and stops. `/apply` takes that typed change and shows what it would do. Only
`commit=true` writes anything.

Splitting them is what makes "every change is previewed before commit"
structural rather than a convention someone can forget.
"""

from __future__ import annotations

import logging
import os
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_settings
from api import actuals, repo, service
from api.db import get_session
from api.models import (
    DispatchApplyRequest,
    DispatchApplyResponse,
    DispatchParseRequest,
    DispatchParseResponse,
)
from api.tables import JOB_STATUSES, SolveRun
from dispatch.apply import apply_change
from dispatch.parse import parse
from solver.model import SolverConfig
from solver.problem import hhmm_to_seconds

log = logging.getLogger("waypoint.routes.dispatch")
router = APIRouter(prefix="/dispatch", tags=["dispatch"])


@router.get("/provider")
async def provider_status() -> dict:
    """Which LLM backend is configured, and can it actually be used.

    Exists so the UI can say "no API key configured" up front rather than
    after the dispatcher has typed a sentence.
    """
    provider = os.environ.get("LLM_PROVIDER", "deepseek").lower()
    if provider == "deepseek":
        ready = bool(os.environ.get("DEEPSEEK_API_KEY"))
        return {
            "provider": "deepseek",
            "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            "ready": ready,
            "reason": None if ready else "DEEPSEEK_API_KEY is not set",
        }
    return {
        "provider": "ollama",
        "model": os.environ.get("OLLAMA_MODEL", "llama3.1"),
        "url": os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434"),
        "ready": True,
        "reason": None,
    }


@router.post("/parse", response_model=DispatchParseResponse)
async def parse_text(
    req: DispatchParseRequest, session: AsyncSession = Depends(get_session)
) -> DispatchParseResponse:
    """Text in, one typed change out. Touches nothing."""
    from api.routes.jobs import list_jobs
    from api.routes.technicians import list_technicians

    techs = [t.model_dump() for t in await list_technicians(session)]
    jobs = [j.model_dump() for j in await list_jobs(day=req.day, session=session)]

    result = await parse(req.text, techs, jobs)
    return DispatchParseResponse(
        understood=result.understood,
        change=result.change,
        error=result.error,
        raw=result.raw,
        provider=result.provider,
    )


@router.post("/apply", response_model=DispatchApplyResponse)
async def apply(
    req: DispatchApplyRequest, session: AsyncSession = Depends(get_session)
) -> DispatchApplyResponse:
    """Preview a typed change against a run, and optionally commit it."""
    row = await session.get(SolveRun, req.run_id)
    if row is None:
        raise HTTPException(404, f"no run {req.run_id}")

    problem, tech_ids, job_ids = await service.build_problem_for(
        session, row.day, include_statuses=JOB_STATUSES
    )
    current = await repo.load_schedule(session, req.run_id, problem)

    # What technicians have reported so far. A typed dispatch change already
    # triggers a re-solve, so folding reality in here means every existing
    # "Ahmad called in sick" also re-plans around who is actually where --
    # rather than only the one path that was built for it.
    reported = await actuals.load_actuals(
        session, run_id=req.run_id, day=row.day, timezone=get_settings().timezone
    )

    cfg = SolverConfig(
        time_limit_s=req.time_limit_s,
        workers=get_settings().solver_workers,
    )
    validated, result = apply_change(
        problem,
        current,
        req.change,
        hhmm_to_seconds(req.now),
        cfg,
        actuals=reported,
    )

    if not validated.ok or result is None:
        return DispatchApplyResponse(
            ok=False,
            reason=validated.reason,
            summary=validated.reason or "the change could not be applied",
            travel_delta_minutes=0,
            unassigned_delta=0,
            customer_calls=0,
            moves=[],
            valid=True,
        )

    new_run_id = None
    if req.commit and result.valid:
        new_run_id = await repo.create_run(
            session,
            row.day,
            {**cfg.as_dict(), "origin": "dispatch", "change": req.change.kind},
            status="running",
        )
        await repo.store_result(
            session,
            new_run_id,
            problem,
            result.after,
            tech_ids,
            job_ids,
            valid=result.valid,
        )
        await session.commit()

    return DispatchApplyResponse(
        ok=True,
        reason=None,
        summary=validated.description,
        travel_delta_minutes=result.travel_delta_s // 60,
        unassigned_delta=result.unassigned_delta,
        customer_calls=result.churn,
        moves=[str(m) for m in result.moves],
        valid=result.valid,
        result=service.to_result(
            problem, result.after, tech_ids, job_ids, new_run_id
        ),
    )
