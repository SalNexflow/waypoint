"""Solver output: what a schedule is, independent of how it was produced.

Deliberately knows nothing about CP-SAT. A Schedule can come from the solver,
from the phase 10 greedy baseline, or from a human typing times into a test
fixture, and every consumer -- the phase 5 checker, the printer, the API --
treats all three identically. That is what makes the checker *independent*:
it validates a Schedule, never a solver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from solver.problem import Problem, hhmm


@dataclass(frozen=True)
class Visit:
    """One job, done by one technician, at a definite time.

    Three timestamps, not one, and the distinction matters:

        arrive_s   the van pulls up
        start_s    work begins
        end_s      work finishes (start_s + duration)

    `start_s` can be later than `arrive_s`. A technician who reaches a site
    before its window opens waits at the kerb -- that is legal, and common
    when a morning-only window follows a short drive. A checker that assumed
    arrival == start would reject perfectly valid schedules, and a model that
    forgot the difference would refuse to let anyone arrive early.
    """

    job_ref: str
    technician_ref: str
    sequence: int
    arrive_s: int
    start_s: int
    end_s: int

    @property
    def wait_s(self) -> int:
        return self.start_s - self.arrive_s


@dataclass(frozen=True)
class Schedule:
    """A complete answer for one day.

    `unassigned` is first-class, not an error condition. The spec is explicit
    that infeasibility is normal: leaving a job out and saying so is a valid
    result, and from phase 6 it is a penalised one rather than a forbidden one.
    """

    day: date
    visits: tuple[Visit, ...]
    unassigned: tuple[str, ...] = ()
    # Free-form provenance: solver status, wall time, matrix source, whether
    # optimality was proved. Kept loose because it is for humans and logs,
    # never for logic.
    meta: dict[str, object] = field(default_factory=dict)

    def by_technician(self) -> dict[str, list[Visit]]:
        """Visits grouped per technician, each list in sequence order."""
        out: dict[str, list[Visit]] = {}
        for v in self.visits:
            out.setdefault(v.technician_ref, []).append(v)
        for visits in out.values():
            visits.sort(key=lambda v: v.sequence)
        return out

    def visit_for(self, job_ref: str) -> Visit | None:
        for v in self.visits:
            if v.job_ref == job_ref:
                return v
        return None

    @property
    def assigned_refs(self) -> frozenset[str]:
        return frozenset(v.job_ref for v in self.visits)


def render(problem: Problem, schedule: Schedule) -> str:
    """Print a schedule as text. The phase 4 deliverable.

    Shows travel, waiting and work explicitly, because those three are what
    you check by eye when deciding whether a schedule is plausible.
    """
    out: list[str] = []
    add = out.append

    routes = schedule.by_technician()
    total_travel = 0
    total_work = 0
    total_wait = 0

    add(f"Schedule for {schedule.day}")
    if schedule.meta:
        bits = "  ".join(f"{k}={v}" for k, v in schedule.meta.items())
        add(f"  {bits}")
    add("")

    for tech in problem.technicians:
        visits = routes.get(tech.ref, [])
        add(f"{tech.ref}  {tech.name}   shift "
            f"{hhmm(tech.shift_start_s)}-{hhmm(tech.shift_end_s)}"
            f"   {len(visits)} job(s)")

        if not visits:
            add("      (idle)")
            add("")
            continue

        node = tech.node
        clock = tech.shift_start_s
        for v in visits:
            job = problem.job(v.job_ref)
            drive = problem.travel_s(node, job.node)
            total_travel += drive
            total_work += job.duration_s
            total_wait += v.wait_s

            origin = "home" if node == tech.node else problem.jobs[
                node - problem.n_techs
            ].ref
            add(f"      drive {origin:>4} -> {job.ref:<4} {drive // 60:>3}min  "
                f"depart {hhmm(clock)}  arrive {hhmm(v.arrive_s)}")
            if v.wait_s > 0:
                add(f"      wait  {v.wait_s // 60:>3}min "
                    f"(window opens {hhmm(job.hard_start_s)})")
            add(f"      WORK  {v.job_ref:<4} {job.name:<24} "
                f"{hhmm(v.start_s)}-{hhmm(v.end_s)}  "
                f"({job.duration_s // 60}min)  "
                f"window {hhmm(job.hard_start_s)}-{hhmm(job.hard_end_s)}")
            node = job.node
            clock = v.end_s

        add(f"      finish {hhmm(clock)}   "
            f"(shift ends {hhmm(tech.shift_end_s)}, "
            f"slack {(tech.shift_end_s - clock) // 60}min)")
        add("")

    if schedule.unassigned:
        add(f"UNASSIGNED ({len(schedule.unassigned)})")
        for ref in schedule.unassigned:
            job = problem.job(ref)
            add(f"      {ref:<4} {job.name:<24} "
                f"window {hhmm(job.hard_start_s)}-{hhmm(job.hard_end_s)}")
        add("")

    add(f"Totals   travel {total_travel // 60}min   work {total_work // 60}min   "
        f"wait {total_wait // 60}min   "
        f"assigned {len(schedule.visits)}/{problem.n_jobs}")

    return "\n".join(out)
