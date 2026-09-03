"""Mid-day re-optimisation.

The part that makes this real rather than a toy. At 11:40 a technician calls in
sick, a job overruns, an emergency comes in. Re-solving from scratch would
reshuffle everything and send people back across the city for a saving that
exists only on paper -- and every customer who was already given a time would
have to be rung back.

So the remaining work re-solves *around* what has already happened:

  **Completed and in-progress jobs are pinned**, technician and start time
  both. They are not removed from the model: leaving them in is what tells the
  solver where each technician physically is and when they become free. A
  technician who finished a job in Klang at 11:20 cannot be in Ampang at
  11:30, and the pinned job is what encodes that.

  **Nothing unpinned may be scheduled in the past.** Without this the solver
  will happily "plan" a 09:00 start at 14:00.

  **Moving a job costs something.** A job already communicated to a customer
  carries a churn penalty, so the objective leaves it alone unless the gain is
  real. Churn is trust, and trust is not free.

The result is reported as a delta, because "here is a new schedule" is not
useful to a dispatcher. "Three jobs moved, two customers need a call, you save
21 minutes" is.
"""

from __future__ import annotations

import dataclasses

from dataclasses import dataclass, field
from enum import Enum

from solver.check import Violation, check
from solver.model import Pin, SolverConfig, solve
from solver.problem import Problem, hhmm
from solver.solution import Schedule, Visit


class JobState(str, Enum):
    DONE = "done"
    IN_PROGRESS = "in_progress"
    PENDING = "pending"


# An actual duration outside this band is not a job, it is a clock.
#
# One minute is faster than anyone gets out of a van, opens a plant room and
# reports arriving; eight hours is longer than a shift. Anything outside it
# came from a phone whose time is wrong -- or from a technician who tapped
# "arrived" in the morning and "complete" after lunch without either being
# true when they said it. Either way it is not a measurement, and a considered
# estimate is a better input to a schedule than a wrong measurement.
MIN_ACTUAL_DURATION_S = 60
MAX_ACTUAL_DURATION_S = 8 * 3600


@dataclass(frozen=True)
class Actual:
    """What a technician reported about one job, in solver time.

    Seconds since local midnight, like everything else the solver sees. The
    database keeps timestamps; they are converted at the boundary
    (api/actuals.py), so by the time they arrive here they are integers on the
    same clock as the schedule.

    Every field is optional because a day is watched as it happens: a job may
    have been left for but not arrived at, arrived at but not finished. What
    is known is used; what is not is left to the estimate.

    `trusted` is False when the server had to clamp the phone's timestamp into
    a believable band (api/field_status.py). Such a report still says the job
    HAPPENED, which is worth pinning -- it does not say WHEN with enough
    confidence to move the rest of the afternoon around.
    """

    job_ref: str
    technician_ref: str
    en_route_s: int | None = None
    arrived_s: int | None = None
    completed_s: int | None = None
    trusted: bool = True

    @property
    def state(self) -> "JobState":
        if self.completed_s is not None:
            return JobState.DONE
        if self.arrived_s is not None:
            return JobState.IN_PROGRESS
        return JobState.PENDING

    @property
    def measured_duration_s(self) -> int | None:
        """How long the work actually took, when that is knowable and sane.

        None unless both ends were reported, in the right order, with a result
        inside the believable band. A negative or absurd figure is DISCARDED
        rather than clamped: the estimate it falls back to is a considered
        number, and a clamped nonsense figure would look just as authoritative
        in the model while being invented.
        """
        if self.arrived_s is None or self.completed_s is None:
            return None
        if not self.trusted:
            return None
        measured = self.completed_s - self.arrived_s
        if measured < MIN_ACTUAL_DURATION_S or measured > MAX_ACTUAL_DURATION_S:
            return None
        return measured


@dataclass(frozen=True)
class Disruption:
    """What changed, and when.

    `now_s` is the moment of re-planning, in seconds since local midnight.
    Everything else is optional and composable -- a sick technician and an
    overrunning job can arrive in the same breath.
    """

    now_s: int
    sick_technicians: frozenset[str] = frozenset()
    # job_ref -> new duration in seconds (an overrun, or a job that turned out
    # to be smaller than expected)
    duration_changes: dict[str, int] = field(default_factory=dict)
    # technician_ref -> new shift end (leaving early)
    shift_changes: dict[str, int] = field(default_factory=dict)
    cancelled_jobs: frozenset[str] = frozenset()
    # Jobs added mid-day. They must already exist in the Problem.
    added_jobs: frozenset[str] = frozenset()

    # WHAT ACTUALLY HAPPENED, straight off the technicians' phones.
    #
    # This is the point of the field app. Without it, re-planning at 11:40
    # assumes every job finished exactly when the morning's solve predicted --
    # so a technician running forty minutes behind is re-planned as though
    # they were on time, and the new schedule is wrong from the moment it is
    # produced.
    actuals: tuple[Actual, ...] = ()

    def actual_for(self, job_ref: str) -> Actual | None:
        for a in self.actuals:
            if a.job_ref == job_ref:
                return a
        return None

    def describe(self) -> str:
        bits = []
        if self.sick_technicians:
            bits.append(f"unavailable: {', '.join(sorted(self.sick_technicians))}")
        for ref, d in sorted(self.duration_changes.items()):
            bits.append(f"{ref} now {d // 60}min")
        for ref, end in sorted(self.shift_changes.items()):
            bits.append(f"{ref} leaves at {hhmm(end)}")
        if self.cancelled_jobs:
            bits.append(f"cancelled: {', '.join(sorted(self.cancelled_jobs))}")
        if self.added_jobs:
            bits.append(f"added: {', '.join(sorted(self.added_jobs))}")
        if self.actuals:
            reported = sum(1 for a in self.actuals if a.state is not JobState.PENDING)
            bits.append(f"{reported} job(s) reported from the field")
        return f"at {hhmm(self.now_s)}: " + ("; ".join(bits) or "no change")


@dataclass(frozen=True)
class Move:
    job_ref: str
    from_technician: str | None
    to_technician: str | None
    from_start_s: int | None
    to_start_s: int | None

    @property
    def kind(self) -> str:
        if self.to_technician is None:
            return "dropped"
        if self.from_technician is None:
            return "added"
        if self.from_technician != self.to_technician:
            return "reassigned"
        return "retimed"

    def __str__(self) -> str:
        if self.kind == "dropped":
            return f"{self.job_ref}: dropped (was {self.from_technician} at {hhmm(self.from_start_s)})"
        if self.kind == "added":
            return f"{self.job_ref}: now assigned to {self.to_technician} at {hhmm(self.to_start_s)}"
        if self.kind == "reassigned":
            return (
                f"{self.job_ref}: {self.from_technician} -> {self.to_technician}, "
                f"{hhmm(self.from_start_s)} -> {hhmm(self.to_start_s)}"
            )
        delta = (self.to_start_s or 0) - (self.from_start_s or 0)
        return (
            f"{self.job_ref}: {self.from_technician} keeps it, "
            f"{hhmm(self.from_start_s)} -> {hhmm(self.to_start_s)} "
            f"({delta // 60:+d}min)"
        )


def drift_by_technician(
    schedule: Schedule, actuals: tuple[Actual, ...]
) -> dict[str, int]:
    """How far behind (or ahead) each technician actually is, in seconds.

    Positive means running late. Measured at the LAST reported job per
    technician, comparing when they really finished against when the schedule
    said they would -- so it answers "how wrong is the rest of this person's
    day" rather than averaging a morning of small slips into meaninglessness.

    This is the number the spec is about: "marks a job done at 10:15 that was
    scheduled to end at 10:00, the system knows the day is running fifteen
    minutes behind". It falls out of the pinning either way -- the schedule
    shifts because the pins moved -- but a number a dispatcher can read beats
    a consequence they have to infer from a redrawn timeline.
    """
    predicted = {v.job_ref: v for v in schedule.visits}
    latest: dict[str, tuple[int, int]] = {}

    for a in actuals:
        if a.completed_s is None or not a.trusted:
            continue
        visit = predicted.get(a.job_ref)
        if visit is None:
            continue
        drift = a.completed_s - visit.end_s
        seen = latest.get(a.technician_ref)
        if seen is None or a.completed_s > seen[0]:
            latest[a.technician_ref] = (a.completed_s, drift)

    return {tech: drift for tech, (_, drift) in latest.items()}


@dataclass(frozen=True)
class ReoptResult:
    before: Schedule
    after: Schedule
    moves: tuple[Move, ...]
    pinned: tuple[str, ...]
    violations: list[Violation]
    disruption: Disruption

    @property
    def drift(self) -> dict[str, int]:
        """technician_ref -> seconds behind schedule, from what they reported."""
        return drift_by_technician(self.before, self.disruption.actuals)

    @property
    def travel_delta_s(self) -> int:
        return int(self.after.meta.get("travel_s", 0)) - int(
            self.before.meta.get("travel_s", 0)
        )

    @property
    def unassigned_delta(self) -> int:
        return len(self.after.unassigned) - len(self.before.unassigned)

    @property
    def churn(self) -> int:
        """Jobs a customer would need to be called about."""
        return sum(1 for m in self.moves if m.kind in ("reassigned", "dropped"))

    @property
    def valid(self) -> bool:
        return not self.violations

    def summary(self) -> str:
        lines = [
            f"Re-optimised {self.disruption.describe()}",
            "",
            f"  pinned            {len(self.pinned)} job(s) already done or under way",
            *(
                [
                    "  running behind    "
                    + ", ".join(
                        f"{tech} {d // 60:+d}min"
                        for tech, d in sorted(self.drift.items())
                        if abs(d) >= 60
                    )
                ]
                if any(abs(d) >= 60 for d in self.drift.values())
                else []
            ),
            f"  moves             {len(self.moves)}",
            f"  customer calls    {self.churn}",
            f"  travel            {self.travel_delta_s // 60:+d}min "
            f"({int(self.before.meta.get('travel_s', 0)) // 60}min -> "
            f"{int(self.after.meta.get('travel_s', 0)) // 60}min)",
            f"  unassigned        {self.unassigned_delta:+d} "
            f"({len(self.before.unassigned)} -> {len(self.after.unassigned)})",
            f"  schedule valid    {self.valid}",
        ]
        if self.moves:
            lines += ["", "Changes"]
            lines += [f"    {m}" for m in self.moves]
        return "\n".join(lines)


def classify(
    schedule: Schedule,
    now_s: int,
    actuals: tuple[Actual, ...] = (),
) -> dict[str, JobState]:
    """Which jobs are done, under way, or still ahead at `now_s`.

    A REPORTED state always beats an inferred one. Without actuals this falls
    back to reading the predicted schedule against the clock -- "it was due to
    end at 10:00 and it is 11:40, so it is done" -- which is a guess, and is
    the guess the field app exists to replace.

    The fallback stays for jobs nobody has reported on, because there is no
    better information for those and assuming a job predicted to finish two
    hours ago is finished beats assuming it never started. Worth knowing that
    this is where the remaining guesswork lives: it shrinks as technicians
    report, and disappears on a day where everyone does.
    """
    reported = {a.job_ref: a.state for a in actuals}

    # WE ONLY GUESS ABOUT PEOPLE WHO ARE NOT TELLING US.
    #
    # For a technician who has reported anything today, silence about a job
    # means they have not done it -- not that they did it and said nothing.
    # Inferring otherwise is both wrong and dangerous, and the danger is the
    # part that is easy to miss:
    #
    # A job predicted 08:03-08:43 that really ran until 09:33 leaves the
    # technician still on site at 08:54, when the schedule had them starting
    # the next one. Time-based inference pins that next job as "done at
    # 08:54" while the actual pins the first as "done at 09:33" -- two pinned
    # jobs overlapping on one person, which is unsatisfiable. The solver
    # returns nothing, and nothing arrives looking like a plan in which every
    # job on the day was dropped.
    #
    # A technician who reports nothing keeps the old inference, because for
    # them there is still no better information.
    reporting = {a.technician_ref for a in actuals}

    out: dict[str, JobState] = {}
    for v in schedule.visits:
        known = reported.get(v.job_ref)
        if known is not None and known is not JobState.PENDING:
            out[v.job_ref] = known
        elif v.technician_ref in reporting:
            out[v.job_ref] = JobState.PENDING
        elif v.end_s <= now_s:
            out[v.job_ref] = JobState.DONE
        elif v.start_s <= now_s:
            out[v.job_ref] = JobState.IN_PROGRESS
        else:
            out[v.job_ref] = JobState.PENDING
    return out


_SETTLED = (JobState.DONE, JobState.IN_PROGRESS)


def _effective_length(visit, actual: Actual | None) -> int:
    """How long a settled job occupied its technician.

    The measurement where there is one, otherwise whatever the schedule
    allowed. Both are seconds on the same clock, so they answer the same
    question: when was this person free again.
    """
    measured = actual.measured_duration_s if actual is not None else None
    return measured if measured is not None else visit.end_s - visit.start_s


def vet_actuals(
    schedule: Schedule,
    now_s: int,
    actuals: tuple[Actual, ...],
    problem: Problem | None = None,
) -> tuple[Actual, ...]:
    """Decide whose reported TIMES can be believed, before anything uses them.

    THIS IS THE LOAD-BEARING FUNCTION OF PHASE 9, and it exists because a
    report is a fact about the world while the model is a set of constraints
    about decisions -- and facts are under no obligation to satisfy those
    constraints, or even each other. Every one of these happened for real
    while building this feature:

      * a job that overran its own SLA window
      * a job that left its technician on site when the schedule had them
        somewhere else, so two pinned jobs overlapped on one person
      * an arrival 59 seconds before the schedule's own start -- earlier than
        the travel matrix says anyone could have got there
      * a technician who forgot to report all morning and tapped through four
        jobs at 14:51, so four addresses shared one arrival minute

    Each produced an unsatisfiable model, and an unsatisfiable model comes
    back as an EMPTY SCHEDULE -- which reads exactly like a considered plan in
    which every job on the day was dropped. That failure is silent, plausible
    and catastrophic, which is why the guard is here rather than in the four
    places that would each have needed their own.

    The rule: a technician's reported times are used only if they could ALL be
    true together -- each job starting no earlier than the previous one could
    have finished, plus the drive in between. If they cannot, that technician
    keeps `trusted=False` on every report, which everything downstream already
    understands to mean *this happened, but not necessarily when*. Their jobs
    still pin, to the right people, in the right order; only the timings fall
    back to the schedule.

    Per technician, so one person catching up on their paperwork does not cost
    everybody else their real timings. Reports already marked untrusted by the
    server's clock clamp (api/field_status.py) stay that way -- this widens
    that same flag rather than inventing a second notion of doubt.
    """
    if not actuals:
        return actuals

    states = classify(schedule, now_s, actuals)
    by_ref = {a.job_ref: a for a in actuals}

    routes: dict[str, list] = {}
    for v in schedule.visits:
        routes.setdefault(v.technician_ref, []).append(v)
    for route in routes.values():
        route.sort(key=lambda v: v.sequence)

    doubted: set[str] = set()
    for tech, route in routes.items():
        settled = [v for v in route if states[v.job_ref] in _SETTLED]
        if not _reports_hold_together(settled, by_ref, problem):
            doubted.add(tech)

    if not doubted:
        return actuals
    return tuple(
        dataclasses.replace(a, trusted=False) if a.technician_ref in doubted else a
        for a in actuals
    )


def _reports_hold_together(
    settled: list,
    by_ref: dict[str, Actual],
    problem: Problem | None,
) -> bool:
    """Could one person really have done these jobs at these reported times?

    Walks the technician's settled jobs in route order, checking each reported
    arrival against the previous job's end plus the drive between them.
    Without a Problem the drive is unknown and only the durations are checked
    -- which still catches the common case of several jobs sharing a minute.

    True when nothing was reported: there is nothing to contradict.
    """
    previous_end: int | None = None
    previous_node: int | None = None

    for visit in settled:
        actual = by_ref.get(visit.job_ref)
        claimed = (
            actual is not None and actual.arrived_s is not None and actual.trusted
        )
        start = max(actual.arrived_s, visit.start_s) if claimed else visit.start_s

        if previous_end is not None:
            travel = 0
            if problem is not None and previous_node is not None:
                try:
                    travel = problem.travel_s(
                        previous_node, problem.job(visit.job_ref).node
                    )
                except KeyError:
                    travel = 0
            if start < previous_end + travel:
                return False

        previous_end = start + _effective_length(visit, actual)
        if problem is not None:
            try:
                previous_node = problem.job(visit.job_ref).node
            except KeyError:
                previous_node = None

    return True


def pins_for(
    schedule: Schedule,
    now_s: int,
    actuals: tuple[Actual, ...] = (),
) -> list[Pin]:
    """Hold fixed what has already happened, at the times it happened.

    Three shapes of pin, and what separates them is how much is known:

    **Done or under way** -- pinned to the technician AND to a time. This is
    the line that makes the whole feature work: a job predicted 09:00-10:00
    that really ran until 10:50 anchors that technician as busy until 10:50,
    so the solver shifts the rest of their day rather than re-planning around
    a morning that did not happen.

    **En route** -- pinned to the TECHNICIAN ONLY, time left free
    (`Pin.start_s=None`). They are in the van and committed, so handing the
    job to somebody else means two people driving to one address. But they
    have not arrived, so nobody knows when it starts, and inventing a time
    would be worse than letting the solver work it out. This is the gap phase
    5 left open, closed by a distinction the Pin model already made.

    **Nothing believable reported** -- pinned to the predicted time, as
    before. `vet_actuals` decides what counts as believable; by the time an
    Actual reaches here, `trusted` already means "these times can be used".
    """
    states = classify(schedule, now_s, actuals)
    by_ref = {a.job_ref: a for a in actuals}

    pins: list[Pin] = []
    for v in schedule.visits:
        actual = by_ref.get(v.job_ref)

        if states[v.job_ref] in _SETTLED:
            start = v.start_s
            if actual is not None and actual.arrived_s is not None and actual.trusted:
                # Never EARLIER than the schedule has it. The model derives the
                # earliest anyone can be anywhere from their shift start and the
                # travel matrix, and the schedule already sits at that boundary
                # -- so an earlier pin asks it to accept an arrival it believes
                # impossible. Arriving late is a delay the day must absorb;
                # arriving early is beating the routing estimate, which is not
                # capacity the model can act on.
                start = max(actual.arrived_s, v.start_s)
            pins.append(Pin(v.job_ref, v.technician_ref, start))
            continue

        if actual is not None and actual.en_route_s is not None:
            pins.append(Pin(v.job_ref, v.technician_ref, None))

    return pins


def durations_from_actuals(actuals: tuple[Actual, ...]) -> dict[str, int]:
    """Measured job durations, for the jobs where one is knowable.

    A finished job's duration stops being an estimate, and feeding the
    measurement back is what stops the same optimistic figure being used for
    the same customer on every visit.
    """
    return {
        a.job_ref: a.measured_duration_s
        for a in actuals
        if a.measured_duration_s is not None
    }


# A retime smaller than this is scheduling noise, not a change anyone acts on.
# Reporting it as a "move" makes the delta look busier than it is.
RETIME_NOISE_S = 60


def diff(before: Schedule, after: Schedule) -> tuple[Move, ...]:
    a = {v.job_ref: v for v in before.visits}
    b = {v.job_ref: v for v in after.visits}
    moves: list[Move] = []
    for ref in sorted(set(a) | set(b)):
        va, vb = a.get(ref), b.get(ref)
        if va and vb:
            same_tech = va.technician_ref == vb.technician_ref
            if same_tech and abs(va.start_s - vb.start_s) < RETIME_NOISE_S:
                continue
            moves.append(
                Move(ref, va.technician_ref, vb.technician_ref, va.start_s, vb.start_s)
            )
        elif va and not vb:
            moves.append(Move(ref, va.technician_ref, None, va.start_s, None))
        elif vb and not va:
            moves.append(Move(ref, None, vb.technician_ref, None, vb.start_s))
    return tuple(moves)


def _durations_for(disruption: Disruption) -> dict[str, int]:
    """Which duration to believe, per job.

    A measurement beats an estimate, and a dispatcher's forecast beats a
    measurement only where the measurement cannot exist yet:

    * **Finished job** -- the measurement is a fact and wins over everything,
      including a dispatcher who said an hour ago that it would overrun. They
      were predicting; the technician was there.
    * **Still in progress, or not started** -- there is nothing to measure, so
      a dispatcher's "this will overrun by an hour" is the newest information
      and wins over the original estimate.
    """
    durations = dict(disruption.duration_changes)
    durations.update(durations_from_actuals(disruption.actuals))
    return durations


def _windows_for(
    problem: Problem,
    pins: "list[Pin]",
    durations: dict[str, int],
    actuals: tuple[Actual, ...],
) -> dict[str, tuple[int, int]]:
    """Widen the hard window of every time-pinned job to admit its pin.

    THE BUG THIS FIXES, because it is not obvious and it is severe.

    A hard window is an SLA: it says when a job MAY be scheduled, and the
    phase 5 checker rejects any schedule that breaks one. Reality does not
    consult it. A job with an 08:00-09:15 window that a technician actually
    worked until 09:33 overran it, and that already happened. Pin it where it
    really was and the model is asked to satisfy a constraint the past has
    already broken -- so it returns nothing, and nothing arrives looking like
    a plan in which every job on the day was dropped.

    A hard window constrains DECISIONS, and for a finished job there is no
    decision left.

    DERIVED FROM THE PINS, not from the raw reports, and that is the second
    half of the fix. The pins are swept forward to keep one technician from
    being in two places at once, so a pin can end up later than what was
    reported. Computing the window from the report instead would open a window
    that no longer covers the pin sitting inside it -- two calculations of the
    same thing, disagreeing, which is exactly how the first attempt failed.
    """
    # ONLY jobs somebody actually reported arriving at.
    #
    # This must never become a way for an ordinary job to escape its SLA. A
    # job pinned by INFERENCE -- "it was due to finish an hour ago, so it is
    # done" -- is a guess, and widening a window on a guess lets the solver
    # park work outside its window and call it legal. Worse, a committed
    # schedule then stores that as fact, and the next re-optimisation widens
    # from there: the violation compounds one run at a time until the
    # independent checker rejects a day nobody did anything wrong on.
    #
    # A report is different. It is a record of something that happened, and
    # the past is not obliged to have respected an SLA.
    reported = {
        a.job_ref
        for a in actuals
        if a.arrived_s is not None and a.trusted
    }

    out: dict[str, tuple[int, int]] = {}
    for pin in pins:
        if pin.start_s is None or pin.job_ref not in reported:
            continue
        try:
            job = problem.job(pin.job_ref)
        except KeyError:
            continue
        length = durations.get(pin.job_ref, job.duration_s)
        out[pin.job_ref] = (pin.start_s, pin.start_s + length)
    return out


def _pin_start(visit, actual: "Actual | None") -> int:
    """Where a pinned job is held, given what was reported about it.

    Extracted so `pins_for` and anything that needs to reason about the same
    number cannot disagree about the floor.
    """
    if actual is None or actual.arrived_s is None or not actual.trusted:
        return visit.start_s
    return max(actual.arrived_s, visit.start_s)


def apply_disruption(
    problem: Problem,
    disruption: Disruption,
    pins: "list[Pin] | None" = None,
) -> Problem:
    """Return a new Problem with the disruption's data changes applied.

    Only touches data -- durations, shifts, which jobs exist. Technician
    availability is handled at solve time via exclude_technicians, because
    removing a technician from the Problem would shift every node index and
    invalidate the travel matrix.
    """
    import dataclasses

    durations = _durations_for(disruption)
    windows = _windows_for(problem, pins or [], durations, disruption.actuals)
    jobs = []
    for j in problem.jobs:
        if j.ref in disruption.cancelled_jobs:
            # Kept in the problem so node indices stay contiguous (Problem
            # enforces that, and the travel matrix is indexed by node), but
            # left structurally UNTOUCHED. It used to have its window
            # collapsed to zero width to make it unschedulable, which tripped
            # the solver's "window shorter than the job" guard and failed the
            # whole solve -- one cancellation emptied the entire day. It is
            # now excluded at solve time via exclude_jobs instead.
            jobs.append(j)
            continue
        if j.ref in durations or j.ref in windows:
            changes: dict[str, int] = {}
            if j.ref in durations:
                changes["duration_s"] = durations[j.ref]
            if j.ref in windows:
                # WIDEN, never narrow. The recorded times join the window
                # rather than replacing it, so a job that ran entirely inside
                # its SLA keeps the window it had.
                started, ended = windows[j.ref]
                changes["hard_start_s"] = min(j.hard_start_s, started)
                changes["hard_end_s"] = max(j.hard_end_s, ended)
            jobs.append(dataclasses.replace(j, **changes))
            continue
        jobs.append(j)

    techs = []
    for t in problem.technicians:
        if t.ref in disruption.shift_changes:
            techs.append(
                dataclasses.replace(t, shift_end_s=disruption.shift_changes[t.ref])
            )
            continue
        techs.append(t)

    return dataclasses.replace(problem, jobs=tuple(jobs), technicians=tuple(techs))


def reoptimise(
    problem: Problem,
    current: Schedule,
    disruption: Disruption,
    config: SolverConfig | None = None,
    *,
    churn_weight: int = 900,
) -> ReoptResult:
    """Re-solve the remainder of the day around what has already happened.

    churn_weight is in the same units as travel seconds, so the default of 900
    says: only move a job that was already promised if doing so saves more
    than 15 minutes of driving. That number is a policy choice, not a physical
    constant -- a company that cares more about keeping its word raises it.
    """
    base = config or SolverConfig()
    cfg = SolverConfig(
        time_limit_s=base.time_limit_s,
        workers=base.workers,
        allow_unassigned=True,
        use_objective=True,
        allowed_overtime_s=base.allowed_overtime_s,
        w_travel=base.w_travel,
        w_unassigned=base.w_unassigned,
        w_overtime=base.w_overtime,
        w_lateness=base.w_lateness,
        w_imbalance=base.w_imbalance,
        w_churn=churn_weight,
    )

    # ONE trust decision, taken once, before anything consumes it.
    #
    # Pins, measured durations, widened windows and the drift figure all read
    # `trusted`. Deciding it per consumer would mean four places that could
    # disagree about whether a report is believable -- and two of them
    # disagreeing is what made the model unsatisfiable the first time.
    disruption = dataclasses.replace(
        disruption,
        actuals=vet_actuals(current, disruption.now_s, disruption.actuals, problem),
    )

    # Pins before the problem: the window a job needs depends on where its pin
    # ended up, so the pins have to exist before the model does.
    pins = pins_for(current, disruption.now_s, disruption.actuals)
    disrupted = apply_disruption(problem, disruption, pins)

    # A cancelled job must not also be pinned. Pins come from what has already
    # happened, so a job that was done or under way at `now` gets pinned to the
    # technician doing it -- but a cancelled job is excluded from every
    # technician's candidate set, and pinning it to someone who can no longer
    # be given it makes the model unbuildable. That failed the whole solve
    # rather than just the cancellation.
    #
    # Cancelling finished work is arguably nonsense anyway; `dispatch/apply.py`
    # refuses it with a readable reason. This is the structural backstop, so
    # any other caller gets a schedule instead of a MODEL_ERROR.
    if disruption.cancelled_jobs:
        pins = [p for p in pins if p.job_ref not in disruption.cancelled_jobs]

    # A sick technician's pinned work stays pinned: what they already did
    # still happened, and it is what tells the solver they are not at their
    # home depot. Only their *future* capacity disappears, which the model
    # handles by giving them no candidates beyond those pins.

    previous = {v.job_ref: v.technician_ref for v in current.visits}

    after = solve(
        disrupted,
        cfg,
        pins=pins,
        previous=previous,
        not_before=disruption.now_s,
        exclude_technicians=set(disruption.sick_technicians),
        exclude_jobs=set(disruption.cancelled_jobs),
        hint=current,
    )

    violations = check(disrupted, after, allowed_overtime_s=cfg.allowed_overtime_s)

    if disruption.cancelled_jobs:
        after = dataclasses.replace(
            after,
            unassigned=tuple(
                r for r in after.unassigned if r not in disruption.cancelled_jobs
            ),
        )

    return ReoptResult(
        before=current,
        after=after,
        moves=diff(current, after),
        pinned=tuple(p.job_ref for p in pins),
        violations=violations,
        disruption=disruption,
    )
