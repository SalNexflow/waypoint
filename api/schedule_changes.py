"""Noticing that somebody's day moved, and telling them.

`/field/today` reads the latest succeeded solve run. That means a
re-optimisation at 11:40 rewrites what a technician is looking at, silently.
This module is the difference between them finding out because the screen
changed and them being told what changed.

WHY NOT solver/reoptimise.py's diff()
-------------------------------------
It computes exactly this delta and I expected to reuse it. It takes two
solver `Schedule` objects, and building one requires a `Problem`, which
requires a travel matrix -- so reusing it would put OSRM in the path that
fires a notification, and make a failed routing provider able to stop a
technician being told their job moved.

The comparison here is two rows-per-job dictionaries out of the assignments
table. One query each, no network, no solver. Same logic, different input
type, and the two have different audiences: `diff()` shows a dispatcher a
preview, this tells a technician about something already committed.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.tables import Assignment, Job, ScheduleChange, SolveRun, Technician

log = logging.getLogger("waypoint.schedule_changes")


class Placement:
    """Where one job sat in one run."""

    __slots__ = ("technician_id", "arrive")

    def __init__(self, technician_id: int, arrive: datetime) -> None:
        self.technician_id = technician_id
        self.arrive = arrive


async def _placements(session: AsyncSession, run_id: int) -> dict[int, Placement]:
    rows = (
        await session.execute(
            select(
                Assignment.job_id,
                Assignment.technician_id,
                Assignment.predicted_arrival,
            ).where(Assignment.solve_run_id == run_id)
        )
    ).all()
    return {job_id: Placement(tech_id, arrive) for job_id, tech_id, arrive in rows}


async def previous_run_id(
    session: AsyncSession, day: date, exclude_run_id: int
) -> int | None:
    """The run a technician would have been looking at before this one.

    "Succeeded" and "not this one". A queued or failed run never became
    anybody's day, so it is not what they are being changed FROM.
    """
    return (
        await session.execute(
            select(SolveRun.id)
            .where(SolveRun.day == day)
            .where(SolveRun.status == "succeeded")
            .where(SolveRun.id != exclude_run_id)
            .order_by(SolveRun.created_at.desc(), SolveRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def detect(
    session: AsyncSession,
    *,
    day: date,
    run_id: int,
    retime_threshold_seconds: int,
) -> int:
    """Compare this run against the one it supersedes and record the deltas.

    Returns how many changes were written. Does NOT commit -- the caller owns
    the transaction that wrote the assignments, and a change row that survived
    a rolled-back schedule would describe something that never happened.

    Emits nothing when there is no previous run. The first schedule of a day
    is not a change; nobody had seen anything to be changed from.
    """
    previous = await previous_run_id(session, day, run_id)
    if previous is None:
        return 0

    before = await _placements(session, previous)
    after = await _placements(session, run_id)
    if not before:
        return 0

    # Names and customers for the sentence the phone will render. One query
    # for every job that moved, rather than one per change.
    touched = sorted(set(before) | set(after))
    jobs = {
        j.id: j
        for j in (
            await session.execute(select(Job).where(Job.id.in_(touched)))
        ).scalars()
    }
    names = dict(
        (
            await session.execute(select(Technician.id, Technician.name))
        ).all()
    )

    written = 0
    for job_id in touched:
        job = jobs.get(job_id)
        if job is None:
            continue

        was = before.get(job_id)
        now = after.get(job_id)

        # Nobody had it and nobody has it. Not a change to anyone.
        if was is None and now is None:
            continue

        base = {
            "customer": job.customer,
            "area": job.area,
            "address": job.address,
        }

        if was is not None and now is not None and was.technician_id == now.technician_id:
            moved = abs((now.arrive - was.arrive).total_seconds())
            if moved < retime_threshold_seconds:
                continue
            _add(
                session,
                technician_id=now.technician_id,
                job_id=job_id,
                kind="retimed",
                run_id=run_id,
                detail={
                    **base,
                    "previous_arrive": was.arrive.isoformat(),
                    "new_arrive": now.arrive.isoformat(),
                },
            )
            written += 1
            continue

        # It left somebody.
        if was is not None:
            # A cancelled job is a different sentence from a reassigned one,
            # and the technician's next action differs: one means "someone
            # else is going", the other means "nobody is".
            cancelled = job.status == "cancelled"
            _add(
                session,
                technician_id=was.technician_id,
                job_id=job_id,
                kind="cancelled" if cancelled else "removed",
                run_id=run_id,
                detail={
                    **base,
                    "previous_arrive": was.arrive.isoformat(),
                    "new_arrive": now.arrive.isoformat() if now else None,
                    "moved_to": (
                        names.get(now.technician_id) if now is not None else None
                    ),
                },
            )
            written += 1

        # And it arrived with somebody.
        if now is not None:
            _add(
                session,
                technician_id=now.technician_id,
                job_id=job_id,
                kind="assigned",
                run_id=run_id,
                detail={
                    **base,
                    "previous_arrive": None,
                    "new_arrive": now.arrive.isoformat(),
                    "moved_from": (
                        names.get(was.technician_id) if was is not None else None
                    ),
                },
            )
            written += 1

    if written:
        log.info("run %s produced %s schedule change(s) on %s", run_id, written, day)
    return written


def _add(
    session: AsyncSession,
    *,
    technician_id: int,
    job_id: int,
    kind: str,
    run_id: int,
    detail: dict,
) -> None:
    session.add(
        ScheduleChange(
            technician_id=technician_id,
            job_id=job_id,
            kind=kind,
            run_id=run_id,
            detail=detail,
        )
    )


async def unacknowledged(
    session: AsyncSession, technician_id: int
) -> list[ScheduleChange]:
    """Oldest first, so a technician reads them in the order they happened."""
    return list(
        (
            await session.execute(
                select(ScheduleChange)
                .where(ScheduleChange.technician_id == technician_id)
                .where(ScheduleChange.acknowledged_at.is_(None))
                .order_by(ScheduleChange.id)
            )
        )
        .scalars()
        .all()
    )
