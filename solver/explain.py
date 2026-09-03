"""Why a job was left unassigned.

"40 jobs did not fit 8 technicians" is not an answer a dispatcher can act on.
"J17 needs a chiller technician and both of yours were committed in Klang by
10am" is. This module turns an unassigned job into that second thing.

Method, in order of cost. The cheap checks are also the definitive ones, so
most jobs never reach the expensive step:

  1. **Nobody has the skills.** Static, certain. Nothing about the rest of the
     day matters.
  2. **Nobody carries the parts.** Same.
  3. **Nobody can physically reach it in its window**, even on an otherwise
     empty day. Static, certain.
  4. Otherwise the job *could* have been done, so it lost to competition.
     Determined by re-solving with that one job forced in. If that solve
     succeeds, the job was droppable and the objective preferred other work --
     and the re-solve tells us what it would have cost. If it fails, the job
     conflicts with something structural.

Step 4 is the only one that runs the solver, and it runs it on a deliberately
short leash.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from solver.model import Pin, SolverConfig, solve
from solver.problem import Problem, ProblemJob, hhmm
from solver.solution import Schedule


class Reason(str, Enum):
    NO_SKILL = "no_technician_has_the_skill"
    NO_PART = "no_van_carries_the_part"
    UNREACHABLE = "unreachable_within_window"
    WINDOW_TOO_SHORT = "window_shorter_than_job"
    CAPACITY = "lost_to_competing_work"
    CONFLICT = "conflicts_with_the_rest_of_the_day"
    UNKNOWN = "undetermined"


@dataclass(frozen=True)
class Explanation:
    job_ref: str
    reason: Reason
    message: str
    # For CAPACITY: what including this job would have cost. None otherwise.
    cost_to_include_s: int | None = None
    displaced: tuple[str, ...] = ()

    def __str__(self) -> str:
        extra = ""
        if self.cost_to_include_s is not None:
            extra = f" (forcing it in costs {self.cost_to_include_s // 60}min extra travel"
            if self.displaced:
                extra += f" and drops {', '.join(self.displaced)}"
            extra += ")"
        return f"{self.job_ref}: {self.message}{extra}"


def explain_job(
    problem: Problem,
    schedule: Schedule,
    job_ref: str,
    *,
    probe: bool = True,
    probe_time_limit_s: float = 5.0,
    config: SolverConfig | None = None,
) -> Explanation:
    """Explain one unassigned job.

    `probe=False` skips the re-solve, returning CAPACITY without detail. The
    API uses probing; the benchmark does not, because N re-solves would dwarf
    the run it is measuring.
    """
    job = problem.job(job_ref)

    if job.latest_start_s < job.hard_start_s:
        return Explanation(
            job_ref,
            Reason.WINDOW_TOO_SHORT,
            f"its window is {(job.hard_end_s - job.hard_start_s) // 60}min "
            f"({hhmm(job.hard_start_s)}-{hhmm(job.hard_end_s)}) but the job "
            f"takes {job.duration_s // 60}min",
        )

    with_skills = [t for t in problem.technicians if problem.has_skills(t, job)]
    if not with_skills:
        needed = ", ".join(sorted(job.skills))
        return Explanation(
            job_ref,
            Reason.NO_SKILL,
            f"needs {needed}; no technician on today has "
            + ("that skill" if len(job.skills) == 1 else "all of those skills"),
        )

    with_parts = [t for t in with_skills if problem.has_parts(t, job)]
    if not with_parts:
        needed = ", ".join(sorted(job.parts))
        who = ", ".join(t.name for t in with_skills)
        return Explanation(
            job_ref,
            Reason.NO_PART,
            f"needs {needed}; {who} "
            f"{'has' if len(with_skills) == 1 else 'have'} the skills but "
            f"{'does' if len(with_skills) == 1 else 'do'} not carry it",
        )

    reachers = [t for t in with_parts if problem.reachable(t, job)]
    if not reachers:
        detail = []
        for t in with_parts:
            drive = problem.travel_s(t.node, job.node)
            arrive = t.shift_start_s + drive
            if arrive > job.latest_start_s:
                detail.append(
                    f"{t.name} cannot arrive before {hhmm(arrive)}, "
                    f"latest start is {hhmm(job.latest_start_s)}"
                )
            else:
                detail.append(
                    f"{t.name}'s shift ends {hhmm(t.shift_end_s)}, too early to finish"
                )
        return Explanation(
            job_ref,
            Reason.UNREACHABLE,
            "no qualified technician can reach it inside its window even on an "
            "empty day: " + "; ".join(detail),
        )

    if not probe:
        return Explanation(
            job_ref,
            Reason.CAPACITY,
            f"{len(reachers)} technician(s) could have done it "
            f"({', '.join(t.name for t in reachers)}) but the day was full",
        )

    # Force it in and see what happens.
    cfg = config or SolverConfig()
    forced = SolverConfig(
        time_limit_s=probe_time_limit_s,
        workers=cfg.workers,
        allow_unassigned=True,
        use_objective=True,
        allowed_overtime_s=cfg.allowed_overtime_s,
        w_travel=cfg.w_travel,
        w_unassigned=cfg.w_unassigned,
        w_overtime=cfg.w_overtime,
        w_lateness=cfg.w_lateness,
    )

    # One probe, not one per technician: force the job to be assigned to
    # *someone* and let the solver pick. Warm-started from the schedule we are
    # explaining, so the rest of the day stays put and the difference that
    # comes back is attributable to this job rather than to the probe having
    # less time than the original solve.
    best = solve(problem, forced, require=[job_ref], hint=schedule)
    if job_ref not in best.assigned_refs:
        best = None

    if best is None:
        return Explanation(
            job_ref,
            Reason.CONFLICT,
            f"{', '.join(t.name for t in reachers)} could reach it in isolation, "
            "but no schedule exists that includes it alongside the rest of the day",
        )

    base_travel = int(schedule.meta.get("travel_s", 0))
    new_travel = int(best.meta.get("travel_s", 0))
    dropped = tuple(sorted(set(best.unassigned) - set(schedule.unassigned)))
    # A probe that dropped more work than it gained did not find a real
    # trade-off, it just ran out of time. Reporting its casualties would be
    # actively misleading, so say nothing rather than something false.
    if len(dropped) > 2:
        return Explanation(
            job_ref,
            Reason.CAPACITY,
            f"could have been done by "
            f"{', '.join(t.name for t in reachers)}, but the day was full; "
            f"fitting it in would require reshuffling {len(dropped)} other jobs",
        )

    return Explanation(
        job_ref,
        Reason.CAPACITY,
        f"could have been done by "
        f"{', '.join(t.name for t in reachers)}, but the day was full",
        cost_to_include_s=max(0, new_travel - base_travel),
        displaced=dropped,
    )


def explain_schedule(
    problem: Problem,
    schedule: Schedule,
    *,
    probe: bool = True,
    probe_time_limit_s: float = 5.0,
    config: SolverConfig | None = None,
    limit: int | None = None,
) -> list[Explanation]:
    """Explain every unassigned job in a schedule."""
    refs = list(schedule.unassigned)
    if limit is not None:
        refs = refs[:limit]
    return [
        explain_job(
            problem,
            schedule,
            ref,
            probe=probe,
            probe_time_limit_s=probe_time_limit_s,
            config=config,
        )
        for ref in refs
    ]


def summarise(explanations: list[Explanation]) -> str:
    if not explanations:
        return "Every job was assigned."
    by_reason: dict[str, int] = {}
    for e in explanations:
        by_reason[e.reason.value] = by_reason.get(e.reason.value, 0) + 1
    lines = [
        f"{len(explanations)} unassigned: "
        + ", ".join(f"{k} x{n}" for k, n in sorted(by_reason.items())),
        "",
    ]
    lines += [f"  {e}" for e in explanations]
    return "\n".join(lines)
