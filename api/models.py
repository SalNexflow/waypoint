"""Request and response schemas.

pydantic, not dataclasses, and the distinction is the point: everything here
crosses a trust boundary. A dataclass would accept a string where an int
belongs and fail somewhere deep in the solver; a pydantic model rejects it at
the edge with a message naming the field.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    UUID4,
    field_validator,
    model_validator,
)


# --- Technicians ------------------------------------------------------------


class TechnicianIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    skills: list[str] = Field(default_factory=list)
    shift_start: str = Field(pattern=r"^\d{2}:\d{2}$", examples=["08:00"])
    shift_end: str = Field(pattern=r"^\d{2}:\d{2}$", examples=["17:00"])
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    van_stock: dict[str, int] = Field(default_factory=dict)
    max_jobs: int = Field(default=8, ge=1, le=50)

    @model_validator(mode="after")
    def shift_must_be_ordered(self):
        """A validator that looks across fields, which a per-field one cannot.

        mode="after" runs once every field has been parsed and coerced, so
        both values are known to be well-formed strings by this point.
        """
        if self.shift_end <= self.shift_start:
            raise ValueError(
                f"shift_end {self.shift_end} must be after shift_start "
                f"{self.shift_start}"
            )
        return self


class TechnicianOut(BaseModel):
    id: int
    name: str
    skills: list[str]
    shift_start: str
    shift_end: str
    lat: float
    lon: float
    van_stock: dict[str, int]
    max_jobs: int


# --- Jobs -------------------------------------------------------------------


class JobIn(BaseModel):
    customer: str = Field(min_length=1, max_length=200)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    duration_minutes: int = Field(ge=5, le=600)
    required_skills: list[str] = Field(default_factory=list)
    required_parts: list[str] = Field(default_factory=list)
    hard_window_start: datetime
    hard_window_end: datetime
    pref_window_start: datetime | None = None
    pref_window_end: datetime | None = None
    # 1 = HIGHEST, 3 = lowest. See api/tables.py for why the default is 3.
    priority: int = Field(default=3, ge=1, le=3)

    @model_validator(mode="after")
    def windows_must_make_sense(self):
        if self.hard_window_end <= self.hard_window_start:
            raise ValueError("hard_window_end must be after hard_window_start")
        span = (self.hard_window_end - self.hard_window_start).total_seconds()
        if span < self.duration_minutes * 60:
            raise ValueError(
                f"hard window is {int(span // 60)}min but the job takes "
                f"{self.duration_minutes}min -- it could never be scheduled"
            )
        if self.pref_window_start and self.pref_window_end:
            if self.pref_window_end <= self.pref_window_start:
                raise ValueError("pref_window_end must be after pref_window_start")
            if (
                self.pref_window_start < self.hard_window_start
                or self.pref_window_end > self.hard_window_end
            ):
                raise ValueError("preferred window must sit inside the hard window")
        return self


class JobOut(BaseModel):
    id: int
    customer: str
    lat: float
    lon: float
    duration_minutes: int
    required_skills: list[str]
    required_parts: list[str]
    hard_window_start: datetime
    hard_window_end: datetime
    pref_window_start: datetime | None
    pref_window_end: datetime | None
    priority: int
    status: str

    # Job detail (field phase 3). Readable here, but deliberately absent from
    # JobIn: `PATCH /jobs/{id}` overwrites every field from the payload, so
    # adding optional inputs would let a partial update silently null an
    # address or a note by omitting it. Setting these has no route yet.
    area: str | None = None
    address: str | None = None
    phone: str | None = None
    service_type: str | None = None
    fault_description: str | None = None
    notes: str | None = None


# --- Solving ----------------------------------------------------------------


class SolveRequest(BaseModel):
    day: date
    time_limit_s: float = Field(default=30.0, ge=1.0, le=600.0)
    workers: int = Field(default=8, ge=1, le=32)
    allowed_overtime_minutes: int = Field(default=0, ge=0, le=240)
    w_travel: int = Field(default=1, ge=0)
    w_unassigned: int = Field(default=1_000_000, ge=0)
    w_overtime: int = Field(default=20, ge=0)
    w_lateness: int = Field(default=3, ge=0)
    w_imbalance: int = Field(default=0, ge=0)

    @field_validator("w_unassigned")
    @classmethod
    def unassigned_should_dominate(cls, v: int, info) -> int:
        """A field_validator runs on one field, after its type coercion.

        This one is advisory rather than strict: a very low unassigned weight
        is legal but almost always a mistake, because it lets the solver drop
        a job to save a few minutes of driving -- which contradicts the whole
        design. Rejecting it outright would stop deliberate experiments, so it
        is allowed and the surprise is documented instead.
        """
        return v


class VisitOut(BaseModel):
    job_id: int
    job_ref: str
    customer: str
    technician_id: int
    technician_ref: str
    technician_name: str
    sequence: int
    arrive: str
    start: str
    end: str
    wait_minutes: int
    lat: float
    lon: float


class RouteOut(BaseModel):
    technician_id: int
    technician_ref: str
    technician_name: str
    shift_start: str
    shift_end: str
    home_lat: float
    home_lon: float
    visits: list[VisitOut]
    travel_minutes: int
    work_minutes: int
    wait_minutes: int


class UnassignedOut(BaseModel):
    job_id: int
    job_ref: str
    customer: str
    lat: float
    lon: float
    reason: str
    message: str


class SolveMetrics(BaseModel):
    status: str
    proved_optimal: bool
    # True when the time limit expired before CP-SAT found anything and the
    # greedy warm start was returned instead. The schedule is valid and
    # workable, but no search happened -- the UI says so rather than passing
    # it off as an optimised day.
    fell_back: bool = False
    objective_value: int | None = None
    travel_minutes: int
    assigned: int
    total_jobs: int
    unassigned_count: int
    solver_wall_ms: int
    matrix_source: str
    reportable: bool
    valid: bool
    violations: list[str] = Field(default_factory=list)


class SolveResultOut(BaseModel):
    run_id: int | None
    day: date
    metrics: SolveMetrics
    routes: list[RouteOut]
    unassigned: list[UnassignedOut]


class SolveRunOut(BaseModel):
    id: int
    day: date
    status: str
    objective_value: int | None
    travel_seconds_total: int | None
    unassigned_count: int | None
    solver_wall_ms: int | None
    proved_optimal: bool | None
    error: str | None
    created_at: datetime


# --- Re-assignment (phase 12) ----------------------------------------------


class ReassignRequest(BaseModel):
    """Drag-to-reassign: move one job to one technician and re-solve."""

    run_id: int
    job_id: int
    technician_id: int
    time_limit_s: float = Field(default=15.0, ge=1.0, le=120.0)
    commit: bool = False


class ReassignPreview(BaseModel):
    ok: bool
    reason: str | None = None
    travel_delta_minutes: int
    unassigned_delta: int
    moved_jobs: list[str]
    customer_calls: int
    # Who actually has to be phoned, by name. `customer_calls` is the count;
    # a dispatcher looking at "3 customer call(s)" needs to know which three
    # before they can decide whether the move is worth making. The rest of
    # `moved_jobs` is same-technician retiming inside the promised window,
    # which nobody rings about.
    calls: list[str] = Field(default_factory=list)
    valid: bool
    result: SolveResultOut | None = None


# --- Natural-language dispatch (phase 13) ----------------------------------

ChangeKind = Literal[
    "remove_technician",
    "add_job",
    "extend_duration",
    "change_shift",
    "change_priority",
    "cancel_job",
]


class DispatchChange(BaseModel):
    """The structured change an LLM is allowed to produce.

    A fixed, closed schema. The parser's only job is to fill this in; it never
    touches the schedule, and anything it cannot express here is rejected
    rather than approximated.
    """

    kind: ChangeKind
    technician_ref: str | None = None
    technician_name: str | None = None
    job_ref: str | None = None
    job_id: int | None = None
    minutes: int | None = None
    new_shift_end: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    priority: int | None = Field(default=None, ge=1, le=3)
    customer: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    required_skills: list[str] = Field(default_factory=list)
    before: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    note: str | None = None

    @model_validator(mode="after")
    def required_fields_per_kind(self):
        """Each change kind needs different fields. Enforced here so an
        underspecified change is rejected at the parser boundary rather than
        blowing up when applied."""
        need: dict[str, list[str]] = {
            "remove_technician": ["technician_ref"],
            "extend_duration": ["job_ref", "minutes"],
            "change_shift": ["technician_ref", "new_shift_end"],
            "change_priority": ["job_ref", "priority"],
            "cancel_job": ["job_ref"],
            "add_job": ["customer"],
        }
        for field_name in need.get(self.kind, []):
            if getattr(self, field_name) is None:
                raise ValueError(
                    f"{self.kind} requires {field_name}, which the parser did "
                    "not supply"
                )
        return self


class DispatchParseRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    day: date
    run_id: int | None = None


class DispatchParseResponse(BaseModel):
    understood: bool
    change: DispatchChange | None = None
    error: str | None = None
    raw: str | None = None
    provider: str


class DispatchApplyRequest(BaseModel):
    run_id: int
    change: DispatchChange
    now: str = Field(default="12:00", pattern=r"^\d{2}:\d{2}$")
    time_limit_s: float = Field(default=20.0, ge=1.0, le=120.0)
    commit: bool = False


class DispatchApplyResponse(BaseModel):
    ok: bool
    reason: str | None = None
    summary: str
    travel_delta_minutes: int
    unassigned_delta: int
    customer_calls: int
    moves: list[str]
    valid: bool
    result: SolveResultOut | None = None


# --- Technician access (field phase 2) --------------------------------------


class AccessCodeOut(BaseModel):
    """The response to issuing a code. Carries the plaintext ONCE.

    `code` is the only place this value ever exists after generation -- the
    database holds a hash. If the dispatcher loses it, they issue another.
    """

    technician_id: int
    technician_name: str
    code: str = Field(examples=["K7M4-XQ2R"])
    expires_at: datetime


class AccessStatusOut(BaseModel):
    """One row of the dispatcher's access screen."""

    technician_id: int
    technician_name: str
    has_live_code: bool
    code_expires_at: datetime | None
    # How many phones currently hold a working token for this technician.
    # Usually 0 or 1; 2+ means an old handset was never revoked.
    active_devices: int


class RevokeAccessOut(BaseModel):
    technician_id: int
    codes_revoked: int
    tokens_revoked: int


class RedeemRequest(BaseModel):
    """What the PWA posts when a technician types their code.

    Accepts a generous string rather than a strict pattern: the API
    normalises case, dashes, spaces and confusable characters itself
    (api/auth.py). Rejecting "k7m4 xq2r" at the schema would be technically
    defensible and practically hostile.
    """

    code: str = Field(min_length=1, max_length=32)


class SessionOut(BaseModel):
    """A redeemed session. The token is returned once and stored by the client."""

    token: str
    technician: TechnicianOut


class TechnicianMeOut(BaseModel):
    """Who the presented token belongs to.

    Exists so the PWA can confirm a stored token is still good, and show the
    technician their own name, without pulling a whole day down.
    """

    id: int
    name: str
    shift_start: str
    shift_end: str


# --- The technician's day (field phase 3) -----------------------------------


class FieldJobOut(BaseModel):
    """One job as the technician's phone sees it.

    Snake_case over the wire, matching every other response in this API. The
    PWA maps to camelCase once, at its own boundary.

    Deliberately FLAT and self-contained: this payload is what phase 6 caches
    for offline use, so every screen must be renderable from it without a
    second request. That is why the detail-screen fields (address, phone,
    fault) are here rather than behind a `GET /field/jobs/{id}` -- a technician
    in a basement can still open a job and see where they are going.
    """

    id: int
    sequence: int
    customer: str
    area: str | None
    address: str | None
    phone: str | None
    service_type: str | None
    fault_description: str | None
    notes: str | None

    lat: float
    lon: float

    # From the assignment: when the solver expects them there, and away.
    arrive: datetime
    depart: datetime
    duration_seconds: int

    # ONE window, not two. The database keeps a hard SLA window and a softer
    # promised window; the technician was never told about the SLA, and
    # showing two windows on a phone invites the question "which one is real".
    # This is the promised window where there is one, the hard window
    # otherwise, and `window_is_promise` says which -- so the detail screen
    # can word it honestly rather than guessing.
    window_start: datetime
    window_end: datetime
    window_is_promise: bool

    parts: list[str]
    status: Literal["upcoming", "en_route", "arrived", "complete"]

    # Whether a completion has been recorded for this job.
    #
    # Distinct from `status == "complete"`: the status comes from the event
    # log and the completion is a separate record, and between the two
    # arriving there is a real window where a job is done but its paperwork
    # is not. The Complete screen uses this to refuse a second submission
    # rather than accepting one the server will silently discard.
    completed: bool = False


class FieldDayOut(BaseModel):
    """A technician's whole day, in visit order."""

    day: date
    technician_id: int
    technician_name: str

    # Which solve produced this schedule. Null means no successful solve for
    # the day yet -- an empty day, not an error. It is also what phase 8 will
    # compare against to notice that a re-solve moved someone's work.
    run_id: int | None

    # The server's clock at the moment of the response.
    #
    # Here from phase 3 rather than added later because it is what lets the
    # client measure its own skew. `occurred_at` on a status event is the
    # PHONE's timestamp -- that is the whole point of working offline -- and a
    # phone that is forty minutes fast would otherwise silently tell
    # re-optimisation the day is forty minutes ahead of where it is.
    server_time: datetime

    # When the last job is expected to finish. The one number the Today screen
    # leads with, computed here so there is a single definition of it.
    #
    # Currently the last assignment's predicted departure, which is a solver
    # prediction and stops being true the moment the day slips. Phase 9 should
    # recompute it forward from the latest real status event.
    finish_estimate: datetime | None

    # Every part code the system knows about, so the Complete screen can offer
    # "we also used..." with the radio off. Eight strings, cached with the day
    # -- cheaper than a second endpoint the technician cannot reach in a
    # basement, and one source of truth instead of a copy in the client that
    # drifts from data/seed/catalog.py.
    parts_catalogue: list[str]

    jobs: list[FieldJobOut]


# --- Status events (field phase 5) ------------------------------------------


class StatusEventIn(BaseModel):
    """One status report from a phone.

    The `id` is generated by the CLIENT, before the request leaves, and it is
    what makes a retry safe: the same event replayed after a timeout carries
    the same id and is discarded server-side rather than recorded twice. A
    server-generated id could not do that, because the client would have no
    way to say "this is the one I already sent".
    """

    id: UUID4
    status: Literal["en_route", "arrived", "complete"]

    # WHEN IT HAPPENED, on the technician's clock. A job completed offline at
    # 10:15 and synced at 11:40 happened at 10:15.
    #
    # Must be timezone-aware. A naive datetime here would be silently read as
    # UTC and land eight hours off in Malaysia -- the same class of mistake as
    # serving job times without an offset, and just as plausible-looking.
    at: AwareDatetime

    # Per-device counter that only ever goes up, so two events sharing a
    # second (or straddling a clock change) still have a defined order.
    # Optional: a client that omits it still works.
    device_seq: int | None = Field(default=None, ge=0)


class StatusEventOut(BaseModel):
    id: UUID4
    job_id: int
    status: str

    # The sanitised time, which may differ from what was sent.
    occurred_at: datetime
    recorded_at: datetime

    # True when the clamp fired -- the phone's clock was outside the
    # believable band and this timestamp should not be trusted for timing.
    time_adjusted: bool

    # True when this id was already on file and nothing new was written. The
    # request still succeeded; the offline queue uses this to tell a genuine
    # first delivery from a replay.
    duplicate: bool

    # The job's furthest-along reported status after this event, which is not
    # necessarily this event's status -- a late `en_route` arriving after a
    # `complete` leaves the job complete.
    job_status: str


# --- Completing a job (field phase 7) ---------------------------------------


class CompletionIn(BaseModel):
    """What the technician filled in on the Complete screen.

    The photo arrives BASE64 IN JSON rather than as a multipart upload.
    Multipart is the idiomatic answer and would need `python-multipart`, which
    this project does not have; base64 costs 33% more bytes and needs nothing
    new. At the sizes involved that is a good trade: the client downscales to
    roughly 300KB before sending, so the overhead is about 100KB, and keeping
    every outbox payload plain JSON means the offline queue built in phase 6
    handles completions with no changes at all.

    It stops being a good trade at full-resolution photos. Swapping to
    multipart is contained to this model, one route and one client function.
    """

    id: UUID4
    parts_used: list[str] = Field(default_factory=list, max_length=40)
    notes: str | None = Field(default=None, max_length=2000)

    # When the WORK finished -- stamped when Complete was tapped, not when the
    # form was submitted. Aware, for the same reason status events are.
    at: AwareDatetime

    # Base64, no data: prefix. None when no photo was taken, which is most of
    # the time.
    photo_base64: str | None = None


class CompletionOut(BaseModel):
    job_id: int
    parts_used: list[str]
    notes: str | None
    photo_key: str | None
    completed_at: datetime
    recorded_at: datetime

    # True when the clamp fired on the client's timestamp.
    time_adjusted: bool

    # True when this job was already completed and nothing was written. The
    # request still succeeded -- the offline queue uses this to tell a first
    # delivery from a replay.
    duplicate: bool


# --- Schedule changes (field phase 8) ---------------------------------------


class ScheduleChangeOut(BaseModel):
    """One thing that moved on this technician's day.

    `detail` is a loose dict on purpose. It carries the customer, the area,
    and the before/after times, and which of those are populated depends on
    the kind -- an `assigned` change has no previous time, a `cancelled` one
    has no new time. Typing every combination would produce four models the
    phone would have to switch on anyway.
    """

    id: int
    job_id: int
    kind: Literal["assigned", "removed", "retimed", "cancelled"]
    detail: dict[str, Any]
    created_at: datetime


class AckRequest(BaseModel):
    """Empty on purpose.

    Acknowledgement carries no information beyond having happened, and the
    change id is in the path. It exists as a model so the route has a body to
    reject nonsense with rather than accepting anything.
    """


# --- Re-optimising around reality (field phase 9) ---------------------------


class ReoptimiseRequest(BaseModel):
    run_id: int
    # Local clock, HH:MM. The moment being re-planned from -- everything
    # already done stays done, nothing unfinished may be scheduled before it.
    now: str = Field(pattern=r"^\d{2}:\d{2}$", examples=["11:40"])
    commit: bool = False
    time_limit_s: float = Field(default=20.0, gt=0, le=300)
    workers: int = Field(default=4, ge=1, le=16)


class ReoptimiseResponse(BaseModel):
    # False when the solver produced nothing, or produced something the
    # independent checker rejected. An infeasible model comes back as an empty
    # schedule, which reads exactly like a plan in which every job was
    # dropped -- "38 moves, 38 customer calls" looks like a decision and was a
    # failure. This is the flag that keeps the two apart.
    ok: bool
    solver_status: str

    # Null on a preview, and also when a commit was refused because the result
    # was not usable. Set when the result was stored as a new run, which makes
    # it the schedule /field/today serves.
    run_id: int | None

    # How many field reports fed into this, and how many were ignored for
    # timing because the phone's clock had to be clamped.
    reported: int
    untrusted: int

    # technician_ref -> minutes behind schedule, positive meaning late. The
    # number the whole field app exists to produce.
    drift_minutes: dict[str, int]

    moves: list[str]
    travel_delta_minutes: int
    unassigned_delta: int
    customer_calls: int
    valid: bool
    summary: str
