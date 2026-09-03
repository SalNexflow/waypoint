"""SQLAlchemy ORM models -- the database schema in Python.

Note on file naming: the spec reserves `models.py` for request/response
schemas (pydantic). Those are a different thing from database tables, so the
tables live here. Keeping them apart matters later: the API must be free to
expose a different shape than it stores.

Time representation
-------------------
Job windows are `timestamptz` -- a job happens on a specific day. Technician
shifts are `time` -- "08:00 to 17:00" is a recurring daily pattern, resolved
against the target date at solve time. Malaysia is UTC+8 with no DST, which
makes that conversion boring, which is exactly what you want.

CP-SAT cannot handle any of these types: it is integer-only. Everything is
converted to *seconds since local midnight* at the solver boundary
(solver/problem.py, phase 4). The database keeps human-meaningful types; the
solver gets integers. Nothing in between mixes the two.
"""

import uuid
from datetime import date, datetime, time
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared parent for every table.

    Alembic reads Base.metadata to know what the schema should look like.
    """


# Integer primary keys rather than UUIDs, deliberately: the natural-language
# layer in phase 13 has to handle "job 4412 will overrun by an hour". A
# dispatcher can say an integer out loud. They cannot say a UUID.

JOB_STATUSES = ("pending", "assigned", "in_progress", "done", "cancelled")
SOLVE_STATUSES = ("queued", "running", "succeeded", "failed")


class Technician(Base):
    __tablename__ = "technicians"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    # text[] -- a real Postgres array, so `'chiller' = ANY(skills)` is an
    # indexable query rather than a JSON scan.
    skills: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'")
    )

    shift_start: Mapped[time] = mapped_column(Time, nullable=False)
    shift_end: Mapped[time] = mapped_column(Time, nullable=False)

    # Route anchor: the technician starts here. Travel from home to the first
    # job counts against the shift; there is no modelled return leg.
    home_location: Mapped[Any] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=False,
    )

    # {"compressor": 2, "gas_r410a": 5}. Quantities are stored from day one so
    # no migration is needed, but the phase 4/6 model only asks whether a part
    # is present. The counting constraint arrives in phase 7.
    van_stock: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    max_jobs: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("8")
    )

    __table_args__ = (
        CheckConstraint("shift_end > shift_start", name="ck_technicians_shift_order"),
        CheckConstraint("max_jobs > 0", name="ck_technicians_max_jobs_positive"),
    )


class Depot(Base):
    __tablename__ = "depots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    location: Mapped[Any] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=False,
    )
    stocked_parts: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[Any] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=False,
    )

    # Seconds, not minutes. One time unit for the whole project; the solver is
    # integer-only and unit drift is a silent, expensive bug.
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)

    required_skills: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'")
    )
    required_parts: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'")
    )

    # HARD window (SLA). Starting outside this is an invalid schedule -- the
    # phase 5 checker rejects it outright.
    hard_window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    hard_window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # PREFERRED window (what was promised to the customer). Missing this is
    # legal but penalised in the objective. Nullable: not every job has one,
    # and when it is absent the lateness term contributes nothing.
    pref_window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pref_window_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 1 = HIGHEST, 2 = normal, 3 = lowest. Defaulting to 3 rather than 1:
    # a job created without an explicit priority is an ordinary job, and
    # under this scale 1 would silently make every such job top priority.
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("3")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'")
    )

    # --- What a technician needs on their phone (field phase 3) -------------
    #
    # None of this is solver input: CP-SAT never reads a street name. It
    # exists because `GET /field/today` promises "customer, address, ...,
    # notes" and the detail screen promises a service type, a fault and a
    # phone number, and the table had a customer name and a point.
    #
    # All nullable. Jobs created before this migration have none, jobs created
    # through `POST /jobs` still have none, and the screens render what is
    # there -- a NOT NULL here would have meant backfilling fiction.

    # Short district label for the Today row: "Setapak". Denormalised rather
    # than derived from `address`, because parsing an address back into an
    # area is exactly the kind of thing that works until it doesn't.
    area: Mapped[str | None] = mapped_column(String(80), nullable=True)

    address: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # E.164. The detail screen turns this into a tel: link, and a local-format
    # number with a leading 0 does not dial reliably from a roaming handset.
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    service_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    fault_description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Note from dispatcher or customer. Sparse by nature -- most jobs have
    # none, and the screen has to look right without one.
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "hard_window_end > hard_window_start", name="ck_jobs_hard_window_order"
        ),
        CheckConstraint(
            "pref_window_start IS NULL "
            "OR pref_window_end IS NULL "
            "OR pref_window_end > pref_window_start",
            name="ck_jobs_pref_window_order",
        ),
        CheckConstraint("duration_seconds > 0", name="ck_jobs_duration_positive"),
        CheckConstraint(
            "status IN ('pending','assigned','in_progress','done','cancelled')",
            name="ck_jobs_status",
        ),
    )


class SolveRun(Base):
    """One invocation of the solver, with the metrics needed to judge it.

    config_snapshot exists so a run stays interpretable months later: it holds
    the objective weights and time limit that produced these numbers. Without
    it the benchmark table in phase 10 is uncomparable across runs.
    """

    __tablename__ = "solve_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'queued'")
    )

    objective_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    travel_seconds_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unassigned_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    solver_wall_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Did CP-SAT prove optimality, or did it hit the time limit with the best
    # solution it had? The spec requires reporting this, and it is the single
    # most important caveat on any number the solver produces.
    proved_optimal: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    config_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed')",
            name="ck_solve_runs_status",
        ),
    )


class Assignment(Base):
    """One job placed on one technician's route, in one solve run."""

    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    solve_run_id: Mapped[int] = mapped_column(
        ForeignKey("solve_runs.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    technician_id: Mapped[int] = mapped_column(
        ForeignKey("technicians.id", ondelete="CASCADE"), nullable=False
    )

    sequence_position: Mapped[int] = mapped_column(Integer, nullable=False)
    predicted_arrival: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    predicted_departure: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Set by re-optimisation (phase 9): a pinned assignment is held fixed
    # because the job is done, in progress, or already promised.
    pinned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    __table_args__ = (
        # A job appears at most once per run, and no two jobs share a slot in
        # one technician's sequence. These are real modelling invariants,
        # enforced by the database rather than trusted from the solver -- the
        # same instinct as the phase 5 checker.
        UniqueConstraint("solve_run_id", "job_id", name="uq_assignments_run_job"),
        UniqueConstraint(
            "solve_run_id",
            "technician_id",
            "sequence_position",
            name="uq_assignments_run_tech_seq",
        ),
        CheckConstraint("sequence_position >= 0", name="ck_assignments_seq_positive"),
        CheckConstraint(
            "predicted_departure >= predicted_arrival",
            name="ck_assignments_time_order",
        ),
    )


class TechnicianAccessCode(Base):
    """A short, sayable code a dispatcher issues to one technician.

    Deliberately NOT the credential the app stores. The code is short enough
    to read down a phone line, which also makes it short enough to guess, so
    it is single-use, expires, and buys exactly one thing: a long random
    bearer token. Two steps rather than one is the whole security argument
    here -- a code that never expired and was stored on the phone forever
    would be a 40-bit password.

    Only the hash is stored. A dispatcher sees the plaintext once, in the
    response to the issue call, and never again.
    """

    __tablename__ = "technician_access_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    technician_id: Mapped[int] = mapped_column(
        ForeignKey("technicians.id", ondelete="CASCADE"), nullable=False
    )

    # sha256 hex. Unique so a collision is a database error rather than a
    # silent cross-technician login.
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Set the moment it is exchanged for a token. Non-null means spent.
    redeemed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Declared here as well as in migration 0003 so `alembic revision
    # --autogenerate` compares like with like. Without these, the next
    # autogenerated revision would cheerfully propose dropping them.
    __table_args__ = (
        Index(
            "ix_technician_access_codes_live",
            "code_hash",
            postgresql_where=text("redeemed_at IS NULL AND revoked_at IS NULL"),
        ),
        Index("ix_technician_access_codes_technician", "technician_id"),
    )


class TechnicianToken(Base):
    """The bearer token a technician's phone actually holds.

    256 bits from `secrets`, stored as a sha256 hash. No expiry: a technician
    forced to log in again mid-shift is a technician standing in a basement
    with no signal and no way back in, which is the exact failure this app
    exists to avoid. Revocation is the control instead, and it is immediate.
    """

    __tablename__ = "technician_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    technician_id: Mapped[int] = mapped_column(
        ForeignKey("technicians.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    # Which code bought this token. Revoking a code revokes its tokens too --
    # without this link, "revoke" would only stop future logins and leave
    # every phone already holding a token still working, which is not what
    # anybody means by the word.
    access_code_id: Mapped[int | None] = mapped_column(
        ForeignKey("technician_access_codes.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "ix_technician_tokens_live",
            "token_hash",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_technician_tokens_technician", "technician_id"),
    )


# The statuses a technician can report, in the order they can only ever move
# THROUGH. The rank is the whole out-of-order defence: a sync that arrives
# late carrying "en_route" cannot demote a job that is already "complete",
# because the derivation takes the highest rank rather than the newest row.
FIELD_STATUSES = ("en_route", "arrived", "complete")
FIELD_STATUS_RANK: dict[str, int] = {"en_route": 1, "arrived": 2, "complete": 3}


class JobStatusEvent(Base):
    """One thing a technician reported about one job. Append-only.

    This table is the source of truth for what has happened on the ground.
    `jobs.status` is a derived cache of it -- see `api/field_status.py` for why
    both exist and how they are kept from disagreeing.

    UUID primary key, client-generated, which BREAKS the project's "integer
    primary keys so a dispatcher can say it out loud" convention. Deliberately:
    nobody ever reads an event id aloud, and the id has a job here that an
    integer cannot do. The phone mints it before the request leaves, so a
    retry after a timeout carries the SAME id and lands as
    `ON CONFLICT DO NOTHING` rather than as a second event. Idempotency is the
    whole reason the client picks the key.

    No `synced` column, though the spec lists one. Whether the phone considers
    a row synced is a fact about the phone, and by definition every row that
    reached this table did. It belongs in IndexedDB (phase 6), not here, where
    it could only ever say "true" and confuse whoever read it.
    """

    __tablename__ = "job_status_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)

    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    technician_id: Mapped[int] = mapped_column(
        ForeignKey("technicians.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    # WHEN IT HAPPENED, on the technician's clock -- the point of the whole
    # design. A job completed offline at 10:15 and synced at 11:40 happened at
    # 10:15, and re-optimisation needs the real time or it re-plans the
    # afternoon around a lie.
    #
    # This is the SANITISED value: clamped into a believable band relative to
    # recorded_at. Everything downstream reads this one.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # What the phone actually claimed, untouched. Kept because the clamp is a
    # judgement and this is the evidence -- `occurred_at != client_occurred_at`
    # says the timestamp was not trusted, which is exactly what phase 9 needs
    # to know before letting a completion time move the schedule.
    client_occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # When the server saw it. Never supplied by the client.
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    # A per-device counter that only ever goes up. Two events queued offline
    # in the same second -- or across a clock change -- need a stable order,
    # and occurred_at alone cannot provide one. Nullable: a client that does
    # not send it still works, it just cannot be ordered as precisely.
    device_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('en_route','arrived','complete')",
            name="ck_job_status_events_status",
        ),
        # The read path: every /field/today folds a technician's events for a
        # handful of jobs into one status each.
        Index("ix_job_status_events_job", "job_id"),
        Index("ix_job_status_events_technician", "technician_id"),
    )


class JobCompletion(Base):
    """What was actually done, filled in on the phone when a job is finished.

    ONE COMPLETION PER JOB, enforced by making `job_id` the primary key rather
    than adding a surrogate one. That is the real-world constraint -- a job is
    finished once -- and expressing it as the key means the insert can be
    `ON CONFLICT DO NOTHING` and a retried upload is a no-op by construction.
    The offline queue retries; without this it would need its own dedup.

    Not append-only, unlike job_status_events, and the difference is worth
    stating. An event is a claim about a moment and there can be many. A
    completion is a single record of what a job needed, and if it is ever
    edited the correct behaviour is to change it, not to accumulate versions
    the dispatcher has to reconcile.
    """

    __tablename__ = "job_completions"

    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    technician_id: Mapped[int] = mapped_column(
        ForeignKey("technicians.id", ondelete="CASCADE"), nullable=False
    )

    # The client's UUID for this completion. Not the key -- job_id is -- but
    # kept because it names the photo file, so a retry writes to the same path
    # instead of leaving an orphan behind.
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Part codes actually used. JSONB rather than text[] to match the spec, and
    # because phase 7's successor will almost certainly want quantities --
    # ["gas_r32"] becomes {"gas_r32": 2} without a type change.
    parts_used: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Filename in the photo store, not a URL. Where the bytes live is the
    # store's business, and today that is a directory on a volume.
    photo_key: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # The technician's clock, clamped the same way a status event's is. This
    # is when the WORK finished -- stamped when they tapped Complete, not when
    # they finished typing the notes.
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    client_completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_job_completions_technician", "technician_id"),
    )


# What a technician gets interrupted about.
SCHEDULE_CHANGE_KINDS = ("assigned", "removed", "retimed", "cancelled")


class ScheduleChange(Base):
    """Something changed on a technician's day after they had already seen it.

    Written by the server when a new solve run supersedes the previous one --
    a re-optimisation, a drag-to-reassign, a dispatcher instruction. Read by
    the phone, shown as a full-screen interrupt, and acknowledged.

    THIS TABLE EXISTS BECAUSE THE DAY IS NOT STABLE. `/field/today` reads the
    latest succeeded run, so a re-solve at 11:40 silently rewrites what a
    technician is looking at. Without a record of the delta, the only way they
    would find out is by noticing the screen had changed -- which is exactly
    the phone call this app is supposed to replace.

    Append-only in practice: rows are inserted and later stamped
    `acknowledged_at`, never edited or deleted. A change that happened
    happened, even after the technician has taken it in.
    """

    __tablename__ = "schedule_changes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    technician_id: Mapped[int] = mapped_column(
        ForeignKey("technicians.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )

    kind: Mapped[str] = mapped_column(String(20), nullable=False)

    # Structured, not a rendered sentence. The phone decides how to word it and
    # at what size, and a stored sentence would also have baked in a timezone
    # and a language at write time.
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Which solve produced this change, kept so a run can be traced back to
    # what it did to people rather than only to its objective value.
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("solve_runs.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('assigned','removed','retimed','cancelled')",
            name="ck_schedule_changes_kind",
        ),
        # The read path: unacknowledged changes for one technician. Partial,
        # because an acknowledged change is never fetched again and would
        # otherwise grow the index forever.
        Index(
            "ix_schedule_changes_unacked",
            "technician_id",
            postgresql_where=text("acknowledged_at IS NULL"),
        ),
    )
