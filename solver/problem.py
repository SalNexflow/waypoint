"""Solver input: pure data, no database, no network, no OR-Tools.

Everything the constraint model needs, in the units CP-SAT can actually use.
Three conversions happen at this boundary and nowhere else:

  1. **Times become integers.** CP-SAT has no floats and no datetimes. Every
     instant is *seconds since local midnight on the day being solved*. A job
     window of 09:00-13:00 is (32400, 46800). Malaysia is UTC+8 with no DST,
     so this conversion is unambiguous.

  2. **Places become node indices.** The travel matrix is addressed by
     integer, so every technician home and job site gets a node number. The
     layout is fixed and documented below.

  3. **Skills and parts become frozensets**, so eligibility is a subset test.

Keeping this module free of OR-Tools is deliberate: it means the problem can
be built, printed, and tested without a solver, and the phase 5 checker can
validate a schedule against it without importing anything that could share a
bug with the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from routing.base import Coord, TravelMatrix


def seconds_since_midnight(t: time) -> int:
    return t.hour * 3600 + t.minute * 60 + t.second


def datetime_to_seconds(dt: datetime, day: date, tz: ZoneInfo) -> int:
    """Seconds from local midnight on `day` to `dt`.

    Can exceed 86400 or go negative if dt is on another day. That is allowed
    and meaningful -- a window ending 01:00 the next morning is 90000 -- but
    for this project everything sits inside one working day.
    """
    local = dt.astimezone(tz)
    midnight = datetime.combine(day, time(0, 0), tzinfo=tz)
    return int((local - midnight).total_seconds())


def hhmm(seconds: int) -> str:
    """Render seconds-since-midnight as HH:MM, for humans reading output."""
    sign = "-" if seconds < 0 else ""
    s = abs(int(seconds))
    return f"{sign}{s // 3600:02d}:{(s % 3600) // 60:02d}"


def hhmm_to_seconds(text: str) -> int:
    """"11:40" -> seconds since local midnight. The inverse of hhmm().

    Here rather than in a route module because two routes now need it, and a
    three-line converter copied into both is exactly how the two drift apart.
    """
    hours, minutes = text.split(":")
    return int(hours) * 3600 + int(minutes) * 60


@dataclass(frozen=True)
class ProblemJob:
    ref: str
    name: str
    node: int
    duration_s: int
    # frozenset, not set: immutable and hashable, so it is safe inside a
    # frozen dataclass and cannot be mutated by accident. Subset tests read
    # directly as `job.skills <= tech.skills`. Python's set algebra has no
    # concise JavaScript equivalent -- JS Sets have no subset operator.
    skills: frozenset[str]
    parts: frozenset[str]
    hard_start_s: int
    hard_end_s: int
    pref_start_s: int | None
    pref_end_s: int | None
    priority: int
    lat: float
    lon: float

    @property
    def latest_start_s(self) -> int:
        """Last moment work can begin and still finish inside the hard window.

        This is the single most useful number for building the model: it is
        the upper bound of the job's start-time variable. Handing CP-SAT a
        tight domain is free constraint propagation.
        """
        return self.hard_end_s - self.duration_s


@dataclass(frozen=True)
class ProblemTech:
    ref: str
    name: str
    node: int
    skills: frozenset[str]
    van_stock: dict[str, int]
    shift_start_s: int
    shift_end_s: int
    max_jobs: int
    lat: float
    lon: float

    @property
    def shift_seconds(self) -> int:
        return self.shift_end_s - self.shift_start_s


@dataclass(frozen=True)
class Problem:
    """One day's dispatch problem.

    Node layout in the travel matrix, fixed and relied upon everywhere:

        node 0 .. T-1      technician homes, in technicians order
        node T .. T+J-1    job sites, in jobs order

    So `problem.travel_s(tech.node, job.node)` is the drive from a
    technician's home to a job, and `travel_s(a.node, b.node)` between two
    jobs. Routes are open: a technician starts at home and ends at the last
    job, with no modelled return leg.
    """

    day: date
    timezone: str
    technicians: tuple[ProblemTech, ...]
    jobs: tuple[ProblemJob, ...]
    travel: TravelMatrix

    # --- lookups ---

    @property
    def n_techs(self) -> int:
        return len(self.technicians)

    @property
    def n_jobs(self) -> int:
        return len(self.jobs)

    def job(self, ref: str) -> ProblemJob:
        for j in self.jobs:
            if j.ref == ref:
                return j
        raise KeyError(f"no job {ref!r}")

    def tech(self, ref: str) -> ProblemTech:
        for t in self.technicians:
            if t.ref == ref:
                return t
        raise KeyError(f"no technician {ref!r}")

    def travel_s(self, from_node: int, to_node: int) -> int:
        return self.travel.duration(from_node, to_node)

    # --- eligibility ---

    def has_skills(self, tech: ProblemTech, job: ProblemJob) -> bool:
        return job.skills <= tech.skills

    def has_parts(self, tech: ProblemTech, job: ProblemJob) -> bool:
        """Phase 4/6 treat van stock as boolean: does the van carry the part.

        The counted version -- a van with 2 compressors can serve at most 2
        jobs needing one -- arrives in phase 7. Keeping it boolean here means
        eligibility is a static property of a (tech, job) pair, which is much
        easier to reason about in a first constraint model.
        """
        return all(p in tech.van_stock for p in job.parts)

    def can_serve(self, tech: ProblemTech, job: ProblemJob) -> bool:
        return self.has_skills(tech, job) and self.has_parts(tech, job)

    def eligible(self, job: ProblemJob) -> tuple[ProblemTech, ...]:
        """Technicians who could do this job at all, ignoring time.

        Worth computing once up front. Every (tech, job) pair this excludes is
        a variable the model never has to create.
        """
        return tuple(t for t in self.technicians if self.can_serve(t, job))

    def reachable(self, tech: ProblemTech, job: ProblemJob) -> bool:
        """Could this technician physically start this job in its window?

        Checks the loosest possible case: leave home at shift start, drive
        straight there, nothing else in the day. If that fails, no schedule
        can include this pair, and the model can skip it entirely.
        """
        earliest = tech.shift_start_s + self.travel_s(tech.node, job.node)
        start = max(earliest, job.hard_start_s)
        return start <= job.latest_start_s and start + job.duration_s <= tech.shift_end_s


def build_problem(
    *,
    day: date,
    timezone: str,
    technicians: list[ProblemTech],
    jobs: list[ProblemJob],
    travel: TravelMatrix,
) -> Problem:
    """Assemble and check a Problem.

    The assertions are cheap and catch the class of bug that is otherwise
    invisible: a node index that does not line up with the travel matrix
    produces a model that solves happily and means nothing.
    """
    expected = len(technicians) + len(jobs)
    if travel.size != expected:
        raise ValueError(
            f"travel matrix has {travel.size} nodes, expected {expected} "
            f"({len(technicians)} technicians + {len(jobs)} jobs)"
        )
    for i, t in enumerate(technicians):
        if t.node != i:
            raise ValueError(f"technician {t.ref} has node {t.node}, expected {i}")
    for i, j in enumerate(jobs):
        if j.node != len(technicians) + i:
            raise ValueError(
                f"job {j.ref} has node {j.node}, expected {len(technicians) + i}"
            )
    return Problem(
        day=day,
        timezone=timezone,
        technicians=tuple(technicians),
        jobs=tuple(jobs),
        travel=travel,
    )


def from_seed_instance(inst, travel: TravelMatrix) -> Problem:
    """Adapt a data.seed SeedInstance into a Problem.

    This is where the generator's human-friendly types (tz-aware datetimes,
    `time` objects) become the solver's integer seconds. Kept here rather than
    in the seed package so that `data/seed` stays independent of the solver.
    """
    tz = ZoneInfo(inst.timezone)

    technicians = [
        ProblemTech(
            ref=t.ref,
            name=t.name,
            node=i,
            skills=frozenset(t.skills),
            van_stock=dict(t.van_stock),
            shift_start_s=seconds_since_midnight(t.shift_start),
            shift_end_s=seconds_since_midnight(t.shift_end),
            max_jobs=t.max_jobs,
            lat=t.home_lat,
            lon=t.home_lon,
        )
        for i, t in enumerate(inst.technicians)
    ]
    offset = len(technicians)
    jobs = [
        ProblemJob(
            ref=j.ref,
            name=j.customer,
            node=offset + i,
            duration_s=j.duration_seconds,
            skills=frozenset(j.required_skills),
            parts=frozenset(j.required_parts),
            hard_start_s=datetime_to_seconds(j.hard_window_start, inst.day, tz),
            hard_end_s=datetime_to_seconds(j.hard_window_end, inst.day, tz),
            pref_start_s=(
                datetime_to_seconds(j.pref_window_start, inst.day, tz)
                if j.pref_window_start
                else None
            ),
            pref_end_s=(
                datetime_to_seconds(j.pref_window_end, inst.day, tz)
                if j.pref_window_end
                else None
            ),
            priority=j.priority,
            lat=j.lat,
            lon=j.lon,
        )
        for i, j in enumerate(inst.jobs)
    ]
    return build_problem(
        day=inst.day,
        timezone=inst.timezone,
        technicians=technicians,
        jobs=jobs,
        travel=travel,
    )


def seed_coords(inst) -> list[Coord]:
    """Coordinates for a SeedInstance in node order, before a Problem exists."""
    return [Coord(t.home_lat, t.home_lon) for t in inst.technicians] + [
        Coord(j.lat, j.lon) for j in inst.jobs
    ]


def coords_for(technicians: list[ProblemTech], jobs: list[ProblemJob]) -> list[Coord]:
    """Coordinates in node order, ready to hand to a travel provider."""
    return [Coord(t.lat, t.lon) for t in technicians] + [
        Coord(j.lat, j.lon) for j in jobs
    ]


def describe(problem: Problem) -> str:
    """Human-readable dump of the problem, before any solving happens."""
    out: list[str] = []
    add = out.append

    add(f"Problem  day={problem.day}  {problem.n_jobs} jobs, "
        f"{problem.n_techs} technicians")
    add(f"         travel matrix: {problem.travel.size} nodes, "
        f"source={problem.travel.source}, reportable={problem.travel.is_reportable}")
    add("")

    add("Technicians")
    for t in problem.technicians:
        add(f"  {t.ref}  {t.name:<16} {hhmm(t.shift_start_s)}-{hhmm(t.shift_end_s)}  "
            f"node={t.node}  max={t.max_jobs}")
        add(f"      skills: {', '.join(sorted(t.skills))}")
        add(f"      van:    {', '.join(f'{k} x{v}' for k, v in sorted(t.van_stock.items()))}")
    add("")

    add("Jobs")
    for j in problem.jobs:
        pref = (
            f"  pref {hhmm(j.pref_start_s)}-{hhmm(j.pref_end_s)}"
            if j.pref_start_s is not None
            else ""
        )
        add(f"  {j.ref}  {j.name:<24} {j.duration_s // 60:>3}min  "
            f"window {hhmm(j.hard_start_s)}-{hhmm(j.hard_end_s)}"
            f"  latest start {hhmm(j.latest_start_s)}{pref}")
        req = []
        if j.skills:
            req.append(f"skills: {', '.join(sorted(j.skills))}")
        if j.parts:
            req.append(f"parts: {', '.join(sorted(j.parts))}")
        if req:
            add(f"      {'  '.join(req)}")
        elig = problem.eligible(j)
        add(f"      eligible: {', '.join(t.ref for t in elig) if elig else 'NOBODY'}")
    add("")

    # Travel matrix, small enough to read at this size.
    labels = [t.ref for t in problem.technicians] + [j.ref for j in problem.jobs]
    add("Travel minutes (row = from, col = to)")
    add("        " + "".join(f"{lbl:>6}" for lbl in labels))
    for i, lbl in enumerate(labels):
        row = "".join(f"{problem.travel_s(i, k) // 60:>6}" for k in range(len(labels)))
        add(f"  {lbl:<6}{row}")

    return "\n".join(out)
