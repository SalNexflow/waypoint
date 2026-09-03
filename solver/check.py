"""Independent feasibility checker.

Validates a Schedule against a Problem without using, importing, or trusting
the solver. That independence is the entire value: a solver will happily
return a schedule that violates a constraint you modelled wrong, and it will
look completely plausible on a map. This is how you find that out.

The rule this module lives by: it must not import solver.model, and it must
not reuse any helper the model uses to build constraints. Where the model says
`start[b] >= start[a] + dur + travel`, this recomputes arrival from first
principles and compares. If both had the same off-by-one, the check would be
worthless.

Every violation carries the numbers, not just a description. "J1 starts at
11:04, latest legal start 11:00, over by 4 minutes" is actionable; "time
window violated" is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from solver.problem import Problem, hhmm
from solver.solution import Schedule


class Code(str, Enum):
    """Violation categories. str-valued so they serialise straight to JSON."""

    UNKNOWN_JOB = "unknown_job"
    UNKNOWN_TECHNICIAN = "unknown_technician"
    DUPLICATE_ASSIGNMENT = "duplicate_assignment"
    MISSING_JOB = "missing_job"
    ASSIGNED_AND_UNASSIGNED = "assigned_and_unassigned"
    BAD_SEQUENCE = "bad_sequence"
    SKILL_MISMATCH = "skill_mismatch"
    PART_MISSING = "part_missing"
    WINDOW_EARLY = "window_early"
    WINDOW_LATE = "window_late"
    DURATION_MISMATCH = "duration_mismatch"
    STARTS_BEFORE_ARRIVAL = "starts_before_arrival"
    TRAVEL_TIME_VIOLATED = "travel_time_violated"
    SHIFT_START_VIOLATED = "shift_start_violated"
    SHIFT_END_VIOLATED = "shift_end_violated"
    OVERLAPPING_JOBS = "overlapping_jobs"
    MAX_JOBS_EXCEEDED = "max_jobs_exceeded"
    NO_SOLUTION = "no_solution"


@dataclass(frozen=True)
class Violation:
    code: Code
    message: str
    job_ref: str | None = None
    technician_ref: str | None = None
    # How badly, in seconds, where that makes sense. Lets a caller sort by
    # severity and lets a test assert on the magnitude rather than the wording.
    magnitude_s: int = 0

    def __str__(self) -> str:
        where = " ".join(x for x in (self.technician_ref, self.job_ref) if x)
        return f"[{self.code.value}] {where}: {self.message}" if where else (
            f"[{self.code.value}] {self.message}"
        )


def check(
    problem: Problem,
    schedule: Schedule,
    allowed_overtime_s: int = 0,
) -> list[Violation]:
    """Return every violation found. Empty list means the schedule is valid.

    Does not stop at the first problem -- a schedule with three faults should
    report three, because fixing one at a time is slower than seeing them all.
    """
    v: list[Violation] = []
    v += _check_solved(problem, schedule)
    v += _check_coverage(problem, schedule)
    v += _check_routes(problem, schedule, allowed_overtime_s)
    return v


# --- Coverage ---------------------------------------------------------------


def _check_solved(problem: Problem, schedule: Schedule) -> list[Violation]:
    """A schedule the solver never produced is not a valid schedule.

    An empty schedule violates no constraint -- nobody drives too far, nobody
    misses a window -- so every other check here passes it. That is correct as
    far as those checks go and dangerously wrong as an answer: a failed solve
    was being reported to a dispatcher as `valid=True` alongside "38 customer
    calls". Whether a solution exists is a different question from whether it
    is legal, and this is where it gets asked.

    A genuinely empty day is only credible if there was nothing to schedule.
    """
    status = str(schedule.meta.get("status", ""))
    if status in ("MODEL_ERROR", "INFEASIBLE"):
        return [
            Violation(
                Code.NO_SOLUTION,
                f"the solver returned no schedule ({status}): "
                f"{schedule.meta.get('reason', 'no reason given')}",
            )
        ]
    if not schedule.visits and problem.jobs:
        return [
            Violation(
                Code.NO_SOLUTION,
                f"nothing was scheduled at all, but the day has "
                f"{len(problem.jobs)} job(s)",
            )
        ]
    return []


def _check_coverage(problem: Problem, schedule: Schedule) -> list[Violation]:
    out: list[Violation] = []
    known_jobs = {j.ref for j in problem.jobs}
    known_techs = {t.ref for t in problem.technicians}

    seen: dict[str, list[str]] = {}
    for visit in schedule.visits:
        if visit.job_ref not in known_jobs:
            out.append(
                Violation(
                    Code.UNKNOWN_JOB,
                    f"schedule references job {visit.job_ref!r}, "
                    "which is not in this problem",
                    job_ref=visit.job_ref,
                )
            )
        if visit.technician_ref not in known_techs:
            out.append(
                Violation(
                    Code.UNKNOWN_TECHNICIAN,
                    f"schedule references technician {visit.technician_ref!r}, "
                    "which is not in this problem",
                    technician_ref=visit.technician_ref,
                )
            )
        seen.setdefault(visit.job_ref, []).append(visit.technician_ref)

    for ref, techs in seen.items():
        if len(techs) > 1:
            out.append(
                Violation(
                    Code.DUPLICATE_ASSIGNMENT,
                    f"assigned {len(techs)} times, to {', '.join(sorted(techs))}",
                    job_ref=ref,
                )
            )

    unassigned = set(schedule.unassigned)
    for ref in sorted(unassigned & set(seen)):
        out.append(
            Violation(
                Code.ASSIGNED_AND_UNASSIGNED,
                "listed as unassigned but also appears in a route",
                job_ref=ref,
            )
        )

    accounted = set(seen) | unassigned
    for ref in sorted(known_jobs - accounted):
        out.append(
            Violation(
                Code.MISSING_JOB,
                "neither assigned nor listed as unassigned",
                job_ref=ref,
            )
        )

    for ref in sorted(unassigned - known_jobs):
        out.append(
            Violation(
                Code.UNKNOWN_JOB,
                f"unassigned list references unknown job {ref!r}",
                job_ref=ref,
            )
        )

    return out


# --- Routes -----------------------------------------------------------------


def _check_routes(
    problem: Problem, schedule: Schedule, allowed_overtime_s: int
) -> list[Violation]:
    out: list[Violation] = []
    known_techs = {t.ref for t in problem.technicians}
    known_jobs = {j.ref for j in problem.jobs}

    for tech_ref, visits in schedule.by_technician().items():
        if tech_ref not in known_techs:
            continue  # already reported by coverage
        tech = problem.tech(tech_ref)

        # Sequence numbers must be 0..n-1 with no gaps or repeats, otherwise
        # "the order" is not well defined and every downstream check is
        # checking a route that does not exist.
        expected = list(range(len(visits)))
        actual = [v.sequence for v in visits]
        if actual != expected:
            out.append(
                Violation(
                    Code.BAD_SEQUENCE,
                    f"sequence positions are {actual}, expected {expected}",
                    technician_ref=tech_ref,
                )
            )

        if len(visits) > tech.max_jobs:
            out.append(
                Violation(
                    Code.MAX_JOBS_EXCEEDED,
                    f"{len(visits)} jobs assigned, limit is {tech.max_jobs}",
                    technician_ref=tech_ref,
                    magnitude_s=0,
                )
            )

        node = tech.node
        clock = tech.shift_start_s
        prev_ref: str | None = None

        for visit in visits:
            if visit.job_ref not in known_jobs:
                continue  # already reported
            job = problem.job(visit.job_ref)

            # -- eligibility ------------------------------------------------
            missing_skills = sorted(job.skills - tech.skills)
            if missing_skills:
                out.append(
                    Violation(
                        Code.SKILL_MISMATCH,
                        f"{tech.name} lacks {', '.join(missing_skills)} "
                        f"(has {', '.join(sorted(tech.skills)) or 'none'})",
                        job_ref=job.ref,
                        technician_ref=tech_ref,
                    )
                )
            missing_parts = sorted(p for p in job.parts if p not in tech.van_stock)
            if missing_parts:
                out.append(
                    Violation(
                        Code.PART_MISSING,
                        f"{tech.name}'s van does not carry "
                        f"{', '.join(missing_parts)} "
                        f"(carries {', '.join(sorted(tech.van_stock)) or 'nothing'})",
                        job_ref=job.ref,
                        technician_ref=tech_ref,
                    )
                )

            # -- internal consistency ---------------------------------------
            actual_duration = visit.end_s - visit.start_s
            if actual_duration != job.duration_s:
                out.append(
                    Violation(
                        Code.DURATION_MISMATCH,
                        f"scheduled for {actual_duration // 60}min, "
                        f"job takes {job.duration_s // 60}min",
                        job_ref=job.ref,
                        technician_ref=tech_ref,
                        magnitude_s=abs(actual_duration - job.duration_s),
                    )
                )

            if visit.start_s < visit.arrive_s:
                out.append(
                    Violation(
                        Code.STARTS_BEFORE_ARRIVAL,
                        f"starts at {hhmm(visit.start_s)} but arrives at "
                        f"{hhmm(visit.arrive_s)}",
                        job_ref=job.ref,
                        technician_ref=tech_ref,
                        magnitude_s=visit.arrive_s - visit.start_s,
                    )
                )

            # -- travel ------------------------------------------------------
            # Recomputed from the matrix, not taken from the schedule. The
            # technician cannot arrive sooner than departing after the
            # previous job and driving.
            need = problem.travel_s(node, job.node)
            allowed = visit.arrive_s - clock
            if allowed < need:
                origin = prev_ref or f"{tech.name}'s home"
                out.append(
                    Violation(
                        Code.TRAVEL_TIME_VIOLATED,
                        f"leg from {origin} needs {need}s "
                        f"({need // 60}min {need % 60}s) but the schedule "
                        f"allows {allowed}s -- short by {need - allowed}s",
                        job_ref=job.ref,
                        technician_ref=tech_ref,
                        magnitude_s=need - allowed,
                    )
                )

            # -- hard time window --------------------------------------------
            if visit.start_s < job.hard_start_s:
                out.append(
                    Violation(
                        Code.WINDOW_EARLY,
                        f"starts {hhmm(visit.start_s)}, window opens "
                        f"{hhmm(job.hard_start_s)}",
                        job_ref=job.ref,
                        technician_ref=tech_ref,
                        magnitude_s=job.hard_start_s - visit.start_s,
                    )
                )
            if visit.end_s > job.hard_end_s:
                out.append(
                    Violation(
                        Code.WINDOW_LATE,
                        f"runs {hhmm(visit.start_s)}-{hhmm(visit.end_s)}, window "
                        f"closes {hhmm(job.hard_end_s)} so the latest legal start "
                        f"is {hhmm(job.latest_start_s)} -- over by "
                        f"{(visit.end_s - job.hard_end_s) // 60}min "
                        f"{(visit.end_s - job.hard_end_s) % 60}s",
                        job_ref=job.ref,
                        technician_ref=tech_ref,
                        magnitude_s=visit.end_s - job.hard_end_s,
                    )
                )

            # -- shift --------------------------------------------------------
            if visit.arrive_s < tech.shift_start_s:
                out.append(
                    Violation(
                        Code.SHIFT_START_VIOLATED,
                        f"arrives {hhmm(visit.arrive_s)}, shift starts "
                        f"{hhmm(tech.shift_start_s)}",
                        job_ref=job.ref,
                        technician_ref=tech_ref,
                        magnitude_s=tech.shift_start_s - visit.arrive_s,
                    )
                )
            cap = tech.shift_end_s + allowed_overtime_s
            if visit.end_s > cap:
                out.append(
                    Violation(
                        Code.SHIFT_END_VIOLATED,
                        f"ends {hhmm(visit.end_s)}, shift ends "
                        f"{hhmm(tech.shift_end_s)}"
                        + (
                            f" (+{allowed_overtime_s // 60}min allowed)"
                            if allowed_overtime_s
                            else ""
                        ),
                        job_ref=job.ref,
                        technician_ref=tech_ref,
                        magnitude_s=visit.end_s - cap,
                    )
                )

            node = job.node
            clock = visit.end_s
            prev_ref = job.ref

        # -- overlap ---------------------------------------------------------
        # Implied by the travel check whenever travel is non-zero, but checked
        # separately so a zero-travel pair (two jobs at the same address)
        # cannot slip through.
        for a, b in zip(visits, visits[1:], strict=False):
            if a.end_s > b.start_s:
                out.append(
                    Violation(
                        Code.OVERLAPPING_JOBS,
                        f"{a.job_ref} runs to {hhmm(a.end_s)} but {b.job_ref} "
                        f"starts at {hhmm(b.start_s)}",
                        job_ref=b.job_ref,
                        technician_ref=tech_ref,
                        magnitude_s=a.end_s - b.start_s,
                    )
                )

    return out


def summarise(violations: list[Violation]) -> str:
    if not violations:
        return "VALID -- no violations found"
    by_code: dict[str, int] = {}
    for v in violations:
        by_code[v.code.value] = by_code.get(v.code.value, 0) + 1
    head = f"INVALID -- {len(violations)} violation(s): " + ", ".join(
        f"{k} x{n}" for k, n in sorted(by_code.items())
    )
    return "\n".join([head, *(f"  {v}" for v in violations)])
