"""Jobs: CRUD and the day view."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_settings
from api.db import get_session
from api.models import JobIn, JobOut
from api.tables import Job
from data.seed.persist import point

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _out(row: Job) -> JobOut:
    from geoalchemy2.shape import to_shape

    p = to_shape(row.location)
    return JobOut(
        id=row.id,
        customer=row.customer,
        lat=p.y,
        lon=p.x,
        duration_minutes=row.duration_seconds // 60,
        required_skills=list(row.required_skills or []),
        required_parts=list(row.required_parts or []),
        hard_window_start=row.hard_window_start,
        hard_window_end=row.hard_window_end,
        pref_window_start=row.pref_window_start,
        pref_window_end=row.pref_window_end,
        priority=row.priority,
        status=row.status,
        area=row.area,
        address=row.address,
        phone=row.phone,
        service_type=row.service_type,
        fault_description=row.fault_description,
        notes=row.notes,
    )


@router.get("", response_model=list[JobOut])
async def list_jobs(
    day: date | None = Query(default=None),
    status: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[JobOut]:
    stmt = select(Job).order_by(Job.id)
    if day is not None:
        tz = ZoneInfo(get_settings().timezone)
        start = datetime.combine(day, time(0, 0), tzinfo=tz)
        stmt = stmt.where(Job.hard_window_start >= start).where(
            Job.hard_window_start < start + timedelta(days=1)
        )
    if status:
        stmt = stmt.where(Job.status == status)
    rows = (await session.execute(stmt)).scalars().all()
    return [_out(r) for r in rows]


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: int, session: AsyncSession = Depends(get_session)
) -> JobOut:
    row = await session.get(Job, job_id)
    if row is None:
        raise HTTPException(404, f"no job {job_id}")
    return _out(row)


@router.post("", response_model=JobOut, status_code=201)
async def create_job(
    payload: JobIn, session: AsyncSession = Depends(get_session)
) -> JobOut:
    row = Job(
        customer=payload.customer,
        location=point(payload.lat, payload.lon),
        duration_seconds=payload.duration_minutes * 60,
        required_skills=payload.required_skills,
        required_parts=payload.required_parts,
        hard_window_start=payload.hard_window_start,
        hard_window_end=payload.hard_window_end,
        pref_window_start=payload.pref_window_start,
        pref_window_end=payload.pref_window_end,
        priority=payload.priority,
        status="pending",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _out(row)


@router.patch("/{job_id}", response_model=JobOut)
async def update_job(
    job_id: int, payload: JobIn, session: AsyncSession = Depends(get_session)
) -> JobOut:
    row = await session.get(Job, job_id)
    if row is None:
        raise HTTPException(404, f"no job {job_id}")
    row.customer = payload.customer
    row.location = point(payload.lat, payload.lon)
    row.duration_seconds = payload.duration_minutes * 60
    row.required_skills = payload.required_skills
    row.required_parts = payload.required_parts
    row.hard_window_start = payload.hard_window_start
    row.hard_window_end = payload.hard_window_end
    row.pref_window_start = payload.pref_window_start
    row.pref_window_end = payload.pref_window_end
    row.priority = payload.priority
    await session.commit()
    await session.refresh(row)
    return _out(row)


@router.delete("/{job_id}", status_code=204, response_class=Response)
async def delete_job(
    job_id: int, session: AsyncSession = Depends(get_session)
) -> Response:
    row = await session.get(Job, job_id)
    if row is None:
        raise HTTPException(404, f"no job {job_id}")
    await session.delete(row)
    await session.commit()
    return Response(status_code=204)
