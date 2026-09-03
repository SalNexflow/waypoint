"""Turning what technicians reported into something the solver can use.

The boundary between two worlds, and the conversion is the whole job:

  * The database keeps `timestamptz` -- absolute instants, with an offset.
  * The solver is integer-only and works in **seconds since local midnight**.

Everything else in this file is about deciding which of those reports deserve
to move a schedule.

WHAT IS AND IS NOT TRUSTED
--------------------------
`job_status_events` stores two times per event: `occurred_at`, clamped into a
believable band by api/field_status.py, and `client_occurred_at`, exactly what
the phone claimed. When they differ, the phone's clock was outside the band
and the server intervened.

A clamped report still says the job HAPPENED -- that is worth knowing, and it
still pins. It does not say WHEN with enough confidence to shift somebody's
afternoon, so `trusted=False` and the clamped time is used for state but not
for timing. This is the guard flagged in phase 5: a phone an hour fast would
otherwise tell re-optimisation the whole day is an hour ahead, and the solver
would rebuild the afternoon on a lie.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.tables import Assignment, JobStatusEvent
from solver.reoptimise import Actual

log = logging.getLogger("waypoint.actuals")


async def load_actuals(
    session: AsyncSession,
    *,
    run_id: int,
    day: date,
    timezone: str,
) -> tuple[Actual, ...]:
    """Every field report for the jobs in this run, as solver-time Actuals.

    Scoped to the run rather than to the day, because an Actual has to name a
    `technician_ref` and that only means something relative to a schedule. A
    job reported by somebody it is no longer assigned to -- reassigned between
    the report and the re-solve -- is dropped: pinning it to the technician
    who used to have it would hold the job on the wrong person.

    Takes the FIRST report of each status, not the last. If a status was
    somehow recorded twice, the first is when the technician said it happened;
    a later duplicate is a retry or a re-tap, and neither moves the moment.
    """
    tz = ZoneInfo(timezone)
    midnight = datetime.combine(day, time(0, 0), tzinfo=tz)

    assigned = dict(
        (
            await session.execute(
                select(Assignment.job_id, Assignment.technician_id).where(
                    Assignment.solve_run_id == run_id
                )
            )
        ).all()
    )
    if not assigned:
        return ()

    rows = (
        await session.execute(
            select(
                JobStatusEvent.job_id,
                JobStatusEvent.technician_id,
                JobStatusEvent.status,
                func.min(JobStatusEvent.occurred_at),
                # Whether the clamp fired on the earliest report of this
                # status. bool_or so one untrusted report taints the status
                # rather than being averaged away.
                func.bool_or(
                    JobStatusEvent.occurred_at != JobStatusEvent.client_occurred_at
                ),
            )
            .where(JobStatusEvent.job_id.in_(list(assigned)))
            .group_by(
                JobStatusEvent.job_id,
                JobStatusEvent.technician_id,
                JobStatusEvent.status,
            )
        )
    ).all()

    def seconds(moment: datetime) -> int:
        """Absolute instant -> seconds since local midnight.

        Negative or beyond 86400 is legal and meaningful: a job reported at
        00:30 on the following day is a genuine overrun past midnight, and
        collapsing it into the day would put it at the start of the morning.
        """
        return int((moment.astimezone(tz) - midnight).total_seconds())

    gathered: dict[int, dict] = {}
    for job_id, technician_id, status, occurred_at, clamped in rows:
        # A report from somebody the job is no longer assigned to says nothing
        # useful about this run.
        if assigned.get(job_id) != technician_id:
            continue
        entry = gathered.setdefault(
            job_id,
            {"technician_id": technician_id, "trusted": True},
        )
        entry[status] = seconds(occurred_at)
        if clamped:
            entry["trusted"] = False

    actuals = tuple(
        Actual(
            job_ref=f"J{job_id}",
            technician_ref=f"T{entry['technician_id']}",
            en_route_s=entry.get("en_route"),
            arrived_s=entry.get("arrived"),
            completed_s=entry.get("complete"),
            trusted=entry["trusted"],
        )
        for job_id, entry in sorted(gathered.items())
    )

    untrusted = sum(1 for a in actuals if not a.trusted)
    if untrusted:
        log.warning(
            "run %s: %s of %s field reports had a clamped timestamp and will "
            "not be used for timing",
            run_id,
            untrusted,
            len(actuals),
        )
    return actuals
