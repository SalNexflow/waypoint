"""The technician-facing API. Everything here is scoped to one person.

Two rules hold for every route in this file, now and in phases 3 onward:

1. **The technician comes from the token, never from the request.** There is
   no `technician_id` parameter anywhere under `/field`. A client cannot ask
   for someone else's day because there is no way to express the question.

2. **Scoping goes in the WHERE clause, not in an `if` after the fetch.** A
   query that structurally cannot return another technician's row cannot be
   forgotten to check. This is also what makes "404, not 403" fall out for
   free: the row simply is not in the result set, so the handler's own
   not-found path answers, and the API never confirms that a job it will not
   show you exists.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from geoalchemy2.shape import to_shape
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api import field_status, photos, repo, schedule_changes
from api.auth import InvalidCode, current_technician, redeem_code
from api.config import get_settings
from api.db import get_session
from api.models import (
    CompletionIn,
    CompletionOut,
    FieldDayOut,
    FieldJobOut,
    ScheduleChangeOut,
    StatusEventIn,
    StatusEventOut,
    RedeemRequest,
    SessionOut,
    TechnicianMeOut,
)
from api.routes.technicians import _hhmm, _out as _technician_out
from api.tables import Assignment, Job, JobCompletion, ScheduleChange, Technician

from data.seed.catalog import PARTS

log = logging.getLogger("waypoint.routes.field")
router = APIRouter(prefix="/field", tags=["field"])


@router.post("/auth/redeem", response_model=SessionOut)
async def redeem(
    payload: RedeemRequest, session: AsyncSession = Depends(get_session)
) -> SessionOut:
    """Exchange a dispatcher-issued access code for a bearer token.

    The only unauthenticated route under /field, necessarily -- it is how a
    phone gets its credential in the first place.

    Answers 401 with one message for every rejection reason. A code that is
    expired, already redeemed, revoked or simply wrong are four different
    facts, and reporting which applies would let an unauthenticated caller
    probe for valid codes. The technician's next step is identical in all four
    cases: ask dispatch for a new one.
    """
    try:
        token, technician = await redeem_code(session, payload.code)
    except InvalidCode:
        await session.rollback()
        raise HTTPException(
            401, "That code isn't valid. Ask dispatch for a new one."
        )

    await session.commit()
    log.info("technician %s (%s) redeemed a code", technician.id, technician.name)
    return SessionOut(token=token, technician=_technician_out(technician))


@router.get("/me", response_model=TechnicianMeOut)
async def me(
    technician: Technician = Depends(current_technician),
) -> TechnicianMeOut:
    """Who this token belongs to.

    The PWA calls it on open to confirm a stored token is still good, and to
    put the technician's own name on the Today screen. Deliberately tiny: it
    must never become the thing standing between the app opening and the
    technician seeing their next job.
    """
    return TechnicianMeOut(
        id=technician.id,
        name=technician.name,
        shift_start=_hhmm(technician.shift_start),
        shift_end=_hhmm(technician.shift_end),
    )


# --- The day ----------------------------------------------------------------

# jobs.status -> what the phone shows.
#
# The two vocabularies do not line up, and saying so explicitly beats a chain
# of ifs somewhere in a template. `pending` and `assigned` are both "not
# started yet" as far as a technician is concerned; the difference between
# them is a dispatcher's concern.
#
# `en_route` has NO source yet. It only becomes expressible in phase 5, when
# job_status_events lands -- `jobs.status` has no value that means "driving
# there", and inventing one would put a state in the solver's status column
# that the solver does not understand. Until then a job the technician is
# driving to still reads as `upcoming`, which is honest.
_STATUS_MAP: dict[str, str] = {
    "pending": "upcoming",
    "assigned": "upcoming",
    "in_progress": "arrived",
    "done": "complete",
}


def _local(dt: datetime, tz: ZoneInfo) -> datetime:
    """Re-express an instant in the dispatch timezone.

    Not a conversion of the moment -- `timestamptz` already stores an absolute
    instant and SQLAlchemy hands it back as an aware datetime in UTC. This
    only changes which offset it is *rendered* with, so the JSON says
    "2026-09-03T08:03:50+08:00" rather than "2026-09-03T00:03:50Z".

    It matters because of what the client does with the string. The PWA reads
    the hour and minute straight out of the ISO text rather than through the
    device's clock, deliberately: a phone left on the wrong timezone -- or on
    UTC, which is what a factory-reset handset does -- must still show
    Malaysian job times. That only works if the offset in the string is the
    Malaysian one. Serving UTC would have every job on the Today screen read
    eight hours early, and it would look plausible.
    """
    return dt.astimezone(tz)


def _local_today(timezone: str) -> date:
    """Today, in the dispatch timezone.

    `date.today()` inside the container is UTC. For eight hours out of every
    twenty-four that is the wrong date in Malaysia -- so between midnight and
    08:00 local, a technician opening the app would be shown yesterday's work.
    """
    return datetime.now(ZoneInfo(timezone)).date()


@router.get("/today", response_model=FieldDayOut)
async def today(
    day: date | None = Query(
        default=None,
        description="Defaults to today in the dispatch timezone.",
    ),
    technician: Technician = Depends(current_technician),
    session: AsyncSession = Depends(get_session),
) -> FieldDayOut:
    """This technician's jobs for the day, in visit order.

    Reads from the latest SUCCEEDED solve run for that date. A queued or
    failed run does not replace a working schedule -- a technician holding a
    day that is being re-solved keeps the one they have until the new one is
    real.

    No solved schedule yet is an empty day, not a 404. "Nothing is scheduled"
    is a true and useful answer; an error would make the app look broken on a
    morning before dispatch has run the solve.

    Scoping is `Assignment.technician_id == technician.id` in the WHERE
    clause. There is no way to ask for anyone else's day, which is what makes
    the "404, not 403" rule structural rather than remembered.
    """
    settings = get_settings()
    tz = ZoneInfo(settings.timezone)
    target = day or _local_today(settings.timezone)

    run_id = await repo.latest_run_id(session, target)
    if run_id is None:
        return FieldDayOut(
            day=target,
            technician_id=technician.id,
            technician_name=technician.name,
            run_id=None,
            server_time=datetime.now(tz),
            finish_estimate=None,
            parts_catalogue=list(PARTS),
            jobs=[],
        )

    # One query, joined, ordered by the solver's own sequence. `.all()` on a
    # two-entity select yields (Assignment, Job) tuples -- SQLAlchemy keeps
    # them as separate objects rather than flattening into one row, so both
    # sides keep their typed attributes.
    rows = (
        await session.execute(
            select(Assignment, Job)
            .join(Job, Job.id == Assignment.job_id)
            .where(Assignment.solve_run_id == run_id)
            .where(Assignment.technician_id == technician.id)
            .where(Job.status != "cancelled")
            .order_by(Assignment.sequence_position)
        )
    ).all()

    # What the technician has actually reported, which OVERRIDES the mapping
    # from jobs.status below. The events table is the truth; jobs.status is a
    # cache of it that deliberately cannot represent `en_route` at all.
    job_ids = [job.id for _, job in rows]
    reported = await field_status.statuses_for_jobs(session, technician.id, job_ids)

    # Which of these already have paperwork. One query for the day rather than
    # a per-job existence check.
    completed_ids: set[int] = set()
    if job_ids:
        completed_ids = set(
            (
                await session.execute(
                    select(JobCompletion.job_id).where(
                        JobCompletion.job_id.in_(job_ids)
                    )
                )
            )
            .scalars()
            .all()
        )

    jobs: list[FieldJobOut] = []
    for assignment, job in rows:
        point = to_shape(job.location)

        # The promised window if the job has one, the SLA window otherwise.
        promised = job.pref_window_start is not None and job.pref_window_end is not None
        jobs.append(
            FieldJobOut(
                id=job.id,
                sequence=assignment.sequence_position,
                customer=job.customer,
                area=job.area,
                address=job.address,
                phone=job.phone,
                service_type=job.service_type,
                fault_description=job.fault_description,
                notes=job.notes,
                lat=point.y,
                lon=point.x,
                arrive=_local(assignment.predicted_arrival, tz),
                depart=_local(assignment.predicted_departure, tz),
                duration_seconds=job.duration_seconds,
                window_start=_local(
                    job.pref_window_start if promised else job.hard_window_start,
                    tz,
                ),
                window_end=_local(
                    job.pref_window_end if promised else job.hard_window_end, tz
                ),
                window_is_promise=promised,
                parts=list(job.required_parts or []),
                # Reported events first; the jobs.status mapping only as a
                # fallback for a job whose state was changed elsewhere (a
                # dispatcher marking it done, say) with no event behind it.
                #
                # An unmapped status would be a new value in the jobs table
                # that nothing here knows about. Falling back to "upcoming"
                # keeps the day renderable rather than 500ing the whole
                # screen over one row.
                status=reported.get(job.id) or _STATUS_MAP.get(job.status, "upcoming"),
                completed=job.id in completed_ids,
            )
        )

    return FieldDayOut(
        day=target,
        technician_id=technician.id,
        technician_name=technician.name,
        run_id=run_id,
        server_time=datetime.now(tz),
        # Already localised: it is the last job's depart, taken from the
        # list above rather than recomputed from the row.
        finish_estimate=jobs[-1].depart if jobs else None,
        parts_catalogue=list(PARTS),
        jobs=jobs,
    )


# --- Reporting a status -----------------------------------------------------


async def _assigned_job(
    session: AsyncSession, job_id: int, technician_id: int, tz: ZoneInfo
) -> Job:
    """The job, if it is currently this technician's. 404 otherwise.

    404 rather than 403, and the same 404 whether the job does not exist,
    belongs to someone else, or was reassigned away five minutes ago. A 403
    would confirm the job exists, which is precisely what the technician is
    not entitled to know.

    "Currently" means: assigned to them in the latest succeeded run for the
    job's own day. That is what makes a reassignment take effect immediately
    -- once dispatch moves a job to someone else and re-solves, the previous
    technician's queued update for it stops being accepted. Phase 8's
    interrupt screen is what turns that from a silent failure into a notice.
    """
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, f"no job {job_id}")

    day = job.hard_window_start.astimezone(tz).date()
    run_id = await repo.latest_run_id(session, day)
    if run_id is None:
        raise HTTPException(404, f"no job {job_id}")

    assigned = (
        await session.execute(
            select(Assignment.id)
            .where(Assignment.solve_run_id == run_id)
            .where(Assignment.job_id == job_id)
            .where(Assignment.technician_id == technician_id)
        )
    ).scalar_one_or_none()
    if assigned is None:
        raise HTTPException(404, f"no job {job_id}")

    return job


@router.post(
    "/jobs/{job_id}/status", response_model=StatusEventOut, status_code=201
)
async def report_status(
    job_id: int,
    payload: StatusEventIn,
    technician: Technician = Depends(current_technician),
    session: AsyncSession = Depends(get_session),
) -> StatusEventOut:
    """Record that this technician moved a job to a new state.

    Idempotent on `payload.id`. Sending the same event twice is a success both
    times and writes once, which is what makes the offline queue in phase 6
    able to retry without counting. The response says which it was.

    Always 201, never 200-vs-201 depending on whether the row was new. The
    status code answers "did this request achieve what it asked for", and a
    replay achieved exactly the same thing as the original.
    """
    tz = ZoneInfo(get_settings().timezone)
    await _assigned_job(session, job_id, technician.id, tz)

    row, already = await field_status.record_event(
        session,
        event_id=payload.id,
        job_id=job_id,
        technician_id=technician.id,
        status=payload.status,
        client_occurred_at=payload.at,
        device_seq=payload.device_seq,
    )

    derived = await field_status.statuses_for_jobs(session, technician.id, [job_id])
    await session.commit()

    log.info(
        "technician %s reported %s on job %s (duplicate=%s)",
        technician.id,
        payload.status,
        job_id,
        already,
    )

    return StatusEventOut(
        id=row.id,
        job_id=row.job_id,
        status=row.status,
        occurred_at=_local(row.occurred_at, tz),
        recorded_at=_local(row.recorded_at, tz),
        time_adjusted=row.occurred_at != row.client_occurred_at,
        duplicate=already,
        job_status=derived.get(job_id, row.status),
    )


# --- Completing a job -------------------------------------------------------


@router.post("/jobs/{job_id}/complete", response_model=CompletionOut, status_code=201)
async def complete_job(
    job_id: int,
    payload: CompletionIn,
    technician: Technician = Depends(current_technician),
    session: AsyncSession = Depends(get_session),
) -> CompletionOut:
    """Record what was actually done, and store the photo if there is one.

    Idempotent on the JOB, not on the payload id: one completion per job is
    the real constraint, so job_id is the primary key and a retry from the
    offline queue lands as ON CONFLICT DO NOTHING. `duplicate` in the response
    says which delivery was the real one.

    Deliberately does NOT write a status event of its own. The phone enqueues
    the `complete` event first and this second, in that order, and fabricating
    an event here would put two records of the same moment in an append-only
    log. If only the completion arrives -- which the ordered queue makes very
    unlikely -- the job carries a completion without being `done`, and lands
    correctly as soon as the event does.
    """
    settings = get_settings()
    tz = ZoneInfo(settings.timezone)
    await _assigned_job(session, job_id, technician.id, tz)

    recorded_at = datetime.now(UTC)
    completed_at = field_status.clamp_occurred_at(payload.at, recorded_at)

    photo_key: str | None = None
    if payload.photo_base64:
        try:
            data = photos.decode(payload.photo_base64, settings.max_photo_bytes)
        except photos.BadPhoto as exc:
            raise HTTPException(422, str(exc)) from exc
        photo_key = photos.key_for(payload.id)
        # Written BEFORE the row. A file with no row is an orphan on a volume;
        # a row pointing at a file that was never written is a broken link the
        # technician sees. Of the two, the orphan is the one nobody notices.
        photos.write(settings.photo_dir, photo_key, data)

    stmt = (
        pg_insert(JobCompletion)
        .values(
            job_id=job_id,
            technician_id=technician.id,
            client_id=payload.id,
            parts_used=payload.parts_used,
            notes=payload.notes or None,
            photo_key=photo_key,
            completed_at=completed_at,
            client_completed_at=payload.at,
            recorded_at=recorded_at,
        )
        .on_conflict_do_nothing(index_elements=["job_id"])
        .returning(JobCompletion.job_id)
    )
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    duplicate = inserted is None

    # Refresh the solver-facing cache from the EVENTS, not from this call. If
    # the `complete` event has already landed the job is already done and this
    # is a no-op; if it has not, this must not mark it done on its own.
    await field_status.refresh_jobs_status(session, job_id)
    await session.commit()

    row = await session.get(JobCompletion, job_id)
    assert row is not None

    log.info(
        "technician %s completed job %s (duplicate=%s, photo=%s)",
        technician.id,
        job_id,
        duplicate,
        bool(row.photo_key),
    )

    return CompletionOut(
        job_id=row.job_id,
        parts_used=list(row.parts_used or []),
        notes=row.notes,
        photo_key=row.photo_key,
        completed_at=_local(row.completed_at, tz),
        recorded_at=_local(row.recorded_at, tz),
        time_adjusted=row.completed_at != row.client_completed_at,
        duplicate=duplicate,
    )


@router.get("/photos/{key}")
async def get_photo(
    key: str,
    technician: Technician = Depends(current_technician),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """A completion photo, if it belongs to this technician.

    Scoped through the completion row rather than inferred from the filename:
    the key is a UUID, and a UUID is unguessable but it is not a permission.
    Same 404-not-403 rule as everywhere else under /field.
    """
    owner = (
        await session.execute(
            select(JobCompletion.technician_id).where(JobCompletion.photo_key == key)
        )
    ).scalar_one_or_none()
    if owner is None or owner != technician.id:
        raise HTTPException(404, "no such photo")

    data = photos.read(get_settings().photo_dir, key)
    if data is None:
        # The row says there is a photo and the volume disagrees. Reported as
        # missing rather than as a server error: the completion is still
        # valid, and a 500 would suggest the whole request was wrong.
        raise HTTPException(404, "no such photo")

    return Response(content=data, media_type="image/jpeg")


# --- Schedule changes -------------------------------------------------------


@router.get("/changes", response_model=list[ScheduleChangeOut])
async def changes(
    technician: Technician = Depends(current_technician),
    session: AsyncSession = Depends(get_session),
) -> list[ScheduleChangeOut]:
    """Everything this technician has not yet been told about.

    Oldest first, so they are read in the order things happened. Scoped by the
    token like everything else here -- there is no technician_id to get wrong.
    """
    tz = ZoneInfo(get_settings().timezone)
    rows = await schedule_changes.unacknowledged(session, technician.id)
    return [
        ScheduleChangeOut(
            id=row.id,
            job_id=row.job_id,
            kind=row.kind,
            detail=_localised_detail(row.detail, tz),
            created_at=_local(row.created_at, tz),
        )
        for row in rows
    ]


def _localised_detail(detail: dict | None, tz: ZoneInfo) -> dict:
    """Re-express the times in `detail` with the Malaysian offset.

    They are STORED as UTC, because an absolute instant is the honest thing to
    keep in a database and rendering one at write time would bake a timezone
    into a row that outlives the decision. They are rendered here, at the
    edge, like every other timestamp under /field -- the phone reads the hour
    straight out of the ISO string, so a handset on the wrong timezone still
    shows Malaysian times, and that only works if the offset is Malaysian.
    """
    out = dict(detail or {})
    for key in ("previous_arrive", "new_arrive"):
        raw = out.get(key)
        if isinstance(raw, str) and raw:
            try:
                out[key] = datetime.fromisoformat(raw).astimezone(tz).isoformat()
            except ValueError:
                # Unparseable: leave it exactly as stored rather than dropping
                # it. A visibly odd timestamp beats a silently missing one.
                pass
    return out


@router.post("/changes/{change_id}/ack", status_code=204, response_class=Response)
async def acknowledge(
    change_id: int,
    technician: Technician = Depends(current_technician),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Mark one change as read.

    Idempotent: acknowledging something already acknowledged is a 204, not a
    conflict. The phone queues these like every other write, so a retry after
    a dead zone is normal rather than exceptional -- and a second 204 is the
    honest answer to "make sure this is acknowledged", which is what the
    request actually means.

    The technician is in the WHERE clause, so a change belonging to somebody
    else simply is not found and answers 404.
    """
    row = (
        await session.execute(
            select(ScheduleChange)
            .where(ScheduleChange.id == change_id)
            .where(ScheduleChange.technician_id == technician.id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, f"no change {change_id}")

    if row.acknowledged_at is None:
        row.acknowledged_at = datetime.now(UTC)
        await session.commit()

    return Response(status_code=204)
