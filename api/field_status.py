"""Recording what a technician reported, and deriving status from it.

The two-column question, settled
--------------------------------
The spec said: keep events append-only, and derive a job's current status
from its latest event rather than storing it in a mutable column.

Append-only: yes. "Not stored in a mutable column": not quite, because
`jobs.status` is already load-bearing. `api/repo.py` filters the solver's
input with `Job.status.in_(("pending","assigned","in_progress"))`. If the
field app only ever wrote events, a completed job would stay `assigned`
forever and every subsequent solve would re-schedule work that is already
done -- the exact opposite of why status reporting exists.

So both, with a clear owner:

* **`job_status_events` is the truth.** Append-only, never updated, never
  deleted. The technician's screen derives from it.
* **`jobs.status` is a derived cache**, written in the same transaction as
  the event, so the solver keeps reading the column it already reads.

The out-of-order protection the spec wanted survives intact, because the
derivation takes the HIGHEST-RANKED event rather than the newest row. A late
sync carrying `en_route` cannot demote a job that is already `complete`. That
rule is enforced twice on purpose: once here in Python for the field view, and
once in SQL for the cache update, where the WHERE clause makes it impossible
for `jobs.status` to move backwards even under concurrent writes.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.tables import FIELD_STATUS_RANK, JobStatusEvent

log = logging.getLogger("waypoint.field_status")


# How far before `recorded_at` a client timestamp may claim to be.
#
# A shift is under twelve hours and the longest believable offline stretch is
# one working day, so anything older than this is a broken clock rather than a
# long dead zone. Clamped rather than rejected: a rejected event is lost work,
# and an approximate time for a job that definitely happened beats no record
# that it happened at all.
MAX_BACKDATE = timedelta(hours=24)


# Field status -> jobs.status, for the solver-facing cache.
#
# `en_route` is deliberately absent. Nothing in the jobs vocabulary means
# "driving there", and `in_progress` is wrong -- solver/reoptimise.py PINS
# in_progress jobs as physically under way, and a technician still in the van
# is not. Leaving the column alone keeps that job movable, which is true.
#
# It is a real gap, not a shrug: an en_route job IS more expensive to move
# than an untouched one, because the technician has committed to it. Phase 9
# is where that becomes a churn-weight question rather than a status one.
_JOBS_STATUS: dict[str, str] = {"arrived": "in_progress", "complete": "done"}


def clamp_occurred_at(
    client_time: datetime, recorded_at: datetime
) -> datetime:
    """Pull a client timestamp into a believable band.

    Phone clocks are wrong -- sometimes by hours, occasionally on purpose once
    someone works out that the app records when they did things. Two rules,
    both about what is physically possible:

    * A time AFTER the server received it cannot have happened. Clamp to the
      moment of receipt.
    * A time more than a day before receipt is not a long dead zone, it is a
      clock that was never set. Clamp to the edge of the band.

    The raw value is stored alongside in `client_occurred_at`, so nothing is
    hidden -- a reader can always see that the clamp fired and by how much.
    """
    if client_time > recorded_at:
        return recorded_at
    floor = recorded_at - MAX_BACKDATE
    if client_time < floor:
        return floor
    return client_time


async def record_event(
    session: AsyncSession,
    *,
    event_id: uuid.UUID,
    job_id: int,
    technician_id: int,
    status: str,
    client_occurred_at: datetime,
    device_seq: int | None,
) -> tuple[JobStatusEvent, bool]:
    """Append one status event. Returns (row, was_already_recorded).

    Idempotent by construction. The insert is
    `INSERT ... ON CONFLICT (id) DO NOTHING`, so a request replayed after a
    timeout -- which is the normal case once the offline queue exists -- lands
    as a no-op rather than a second event. The alternative, SELECT-then-INSERT,
    races against its own retry: two in-flight copies can both find nothing
    and both insert.

    `pg_insert` rather than SQLAlchemy's generic insert: ON CONFLICT is
    Postgres-specific syntax and the dialect-specific constructor is what
    exposes it.
    """
    recorded_at = datetime.now(UTC)
    occurred_at = clamp_occurred_at(client_occurred_at, recorded_at)

    if occurred_at != client_occurred_at:
        log.warning(
            "clamped occurred_at for event %s: client said %s, recorded %s",
            event_id,
            client_occurred_at.isoformat(),
            recorded_at.isoformat(),
        )

    stmt = (
        pg_insert(JobStatusEvent)
        .values(
            id=event_id,
            job_id=job_id,
            technician_id=technician_id,
            status=status,
            occurred_at=occurred_at,
            client_occurred_at=client_occurred_at,
            recorded_at=recorded_at,
            device_seq=device_seq,
        )
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(JobStatusEvent.id)
    )
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    already = inserted is None

    # The cache is refreshed even on a duplicate. Costs one statement and
    # closes a real hole: if the first attempt inserted the event and then the
    # connection dropped before the cache update committed, the retry is the
    # only chance to make them agree again.
    await refresh_jobs_status(session, job_id)

    row = await session.get(JobStatusEvent, event_id)
    assert row is not None  # inserted just now, or already present
    return row, already


async def refresh_jobs_status(session: AsyncSession, job_id: int) -> None:
    """Point `jobs.status` at the highest-ranked event for this job.

    The WHERE clause is the important half. It compares the rank of the
    CURRENT column value against the rank of the new one and only writes when
    the new one is higher, so `jobs.status` physically cannot move backwards
    -- not from an out-of-order sync, not from two concurrent writes
    interleaving, not from a replay of an old event. Expressing it in SQL
    rather than as an `if` in Python is what makes that true regardless of
    what else is happening in the database at the time.

    `cancelled` is excluded outright. A cancelled job is a dispatcher
    decision, and a technician's status report should not quietly resurrect
    it.
    """
    statuses = (
        (
            await session.execute(
                select(JobStatusEvent.status).where(JobStatusEvent.job_id == job_id)
            )
        )
        .scalars()
        .all()
    )
    highest = highest_status(statuses)
    target = _JOBS_STATUS.get(highest or "")
    if target is None:
        # Only en_route so far (or nothing at all): the solver's view of this
        # job has not changed.
        return

    await session.execute(
        text(
            """
            UPDATE jobs
               SET status = :target
             WHERE id = :job_id
               AND status <> 'cancelled'
               AND CASE status
                     WHEN 'in_progress' THEN 1
                     WHEN 'done'        THEN 2
                     ELSE 0
                   END
                 < CASE :target
                     WHEN 'in_progress' THEN 1
                     WHEN 'done'        THEN 2
                     ELSE 0
                   END
            """
        ),
        {"target": target, "job_id": job_id},
    )


def highest_status(statuses: Iterable[str]) -> str | None:
    """The furthest-along status in a set of events. None if there are none.

    `max(..., key=...)` with a default, rather than sorting: the question is
    "which of these got furthest", and rank order is not arrival order. An
    unknown status ranks 0 so a value written by a future version of the app
    cannot outrank a real one.
    """
    known = [s for s in statuses if s in FIELD_STATUS_RANK]
    if not known:
        return None
    return max(known, key=lambda s: FIELD_STATUS_RANK[s])


async def statuses_for_jobs(
    session: AsyncSession, technician_id: int, job_ids: list[int]
) -> dict[int, str]:
    """job_id -> furthest-along reported status, for one technician's day.

    One query for the whole day, folded in Python, rather than a correlated
    subquery per job. At a dozen jobs the difference is not performance, it is
    that the fold is readable and the lateral join is not.
    """
    if not job_ids:
        return {}

    rows = (
        await session.execute(
            select(JobStatusEvent.job_id, JobStatusEvent.status)
            .where(JobStatusEvent.technician_id == technician_id)
            .where(JobStatusEvent.job_id.in_(job_ids))
        )
    ).all()

    by_job: dict[int, list[str]] = {}
    for job_id, status in rows:
        by_job.setdefault(job_id, []).append(status)

    out: dict[int, str] = {}
    for job_id, statuses in by_job.items():
        best = highest_status(statuses)
        if best is not None:
            out[job_id] = best
    return out
