"""Validate a parsed change, apply it, re-optimise, and describe what moved.

Everything from here on is deterministic. The LLM's output arrived as a typed
`DispatchChange` and is validated against the real day before anything happens
-- a change naming a technician who is not working today is rejected here, not
half-applied.
"""

from __future__ import annotations

import dataclasses

from dataclasses import dataclass

from api.models import DispatchChange
from solver.check import Code
from solver.model import SolverConfig
from solver.problem import Problem, seconds_since_midnight
from solver.reoptimise import Actual, Disruption, ReoptResult, reoptimise
from solver.solution import Schedule


@dataclass(frozen=True)
class Validated:
    ok: bool
    reason: str | None
    disruption: Disruption | None
    description: str


def _time_to_seconds(hhmm_str: str) -> int:
    h, m = hhmm_str.split(":")
    return int(h) * 3600 + int(m) * 60


def resolve_technician(problem: Problem, change: DispatchChange) -> str | None:
    """Find the technician a change refers to, by ref or by name.

    Name matching is deliberately strict about ambiguity: if "Lee" matches two
    people, that is an error rather than a coin flip. The cost of guessing
    wrong here is a real person's day being rewritten.
    """
    if change.technician_ref:
        try:
            problem.tech(change.technician_ref)
            return change.technician_ref
        except KeyError:
            pass

    name = (change.technician_name or "").strip().lower()
    if not name:
        return None
    matches = [t for t in problem.technicians if name in t.name.lower()]
    if len(matches) == 1:
        return matches[0].ref
    return None


def validate(
    problem: Problem, schedule: Schedule, change: DispatchChange, now_s: int
) -> Validated:
    """Turn a DispatchChange into a Disruption, or explain why it cannot be."""
    kind = change.kind

    if kind == "remove_technician":
        ref = resolve_technician(problem, change)
        if ref is None:
            who = change.technician_name or change.technician_ref or "that technician"
            # Only look for name collisions when a name was actually given.
            # An empty string is a substring of every name, so without this
            # guard an unknown *ref* reports as "ambiguous" listing the whole
            # team -- confidently wrong, which is the worst kind of message.
            name = (change.technician_name or "").strip().lower()
            candidates = (
                [t.name for t in problem.technicians if name in t.name.lower()]
                if name
                else []
            )
            reason = (
                f"{who} is ambiguous -- could be {', '.join(candidates)}"
                if len(candidates) > 1
                else f"{who} is not working today"
            )
            return Validated(False, reason, None, "")
        tech = problem.tech(ref)
        remaining = [
            v for v in schedule.visits
            if v.technician_ref == ref and v.end_s > now_s
        ]
        return Validated(
            True,
            None,
            Disruption(now_s=now_s, sick_technicians=frozenset({ref})),
            f"Remove {tech.name} from the rest of the day and redistribute "
            f"their {len(remaining)} remaining job(s).",
        )

    if kind == "change_shift":
        ref = resolve_technician(problem, change)
        if ref is None:
            return Validated(
                False,
                f"{change.technician_name or change.technician_ref} "
                "is not working today",
                None,
                "",
            )
        tech = problem.tech(ref)
        new_end = _time_to_seconds(change.new_shift_end or "17:00")
        if new_end <= tech.shift_start_s:
            return Validated(
                False,
                f"{change.new_shift_end} is before {tech.name}'s shift starts",
                None,
                "",
            )
        return Validated(
            True,
            None,
            Disruption(now_s=now_s, shift_changes={ref: new_end}),
            f"{tech.name} now finishes at {change.new_shift_end} "
            f"instead of {tech.shift_end_s // 3600:02d}:"
            f"{(tech.shift_end_s % 3600) // 60:02d}.",
        )

    if kind in ("extend_duration", "change_priority", "cancel_job"):
        ref = change.job_ref or (f"J{change.job_id}" if change.job_id else None)
        if ref is None:
            return Validated(False, "no job was identified", None, "")
        try:
            job = problem.job(ref)
        except KeyError:
            return Validated(False, f"{ref} is not on today's schedule", None, "")

        if kind == "extend_duration":
            delta = (change.minutes or 0) * 60
            if delta == 0:
                return Validated(False, "no duration change was given", None, "")
            new_duration = job.duration_s + delta
            if new_duration <= 0:
                return Validated(
                    False, "that would make the job take no time at all", None, ""
                )
            window = job.hard_end_s - job.hard_start_s
            if new_duration > window:
                return Validated(
                    False,
                    f"{job.name} would take {new_duration // 60}min but its "
                    f"window is only {window // 60}min -- it could never fit",
                    None,
                    "",
                )
            return Validated(
                True,
                None,
                Disruption(now_s=now_s, duration_changes={ref: new_duration}),
                f"{job.name} ({ref}) now takes {new_duration // 60}min "
                f"instead of {job.duration_s // 60}min.",
            )

        if kind == "cancel_job":
            return Validated(
                True,
                None,
                Disruption(now_s=now_s, cancelled_jobs=frozenset({ref})),
                f"Cancel {job.name} ({ref}) and free up its slot.",
            )

        # change_priority does not alter feasibility, only preference. Applied
        # as a plain re-solve so the dispatcher still sees a preview.
        return Validated(
            True,
            None,
            Disruption(now_s=now_s),
            f"{job.name} ({ref}) priority set to {change.priority}. "
            "Priority influences ordering, not feasibility.",
        )

    if kind == "add_job":
        # Adding a job means creating a row first, which is a write the
        # preview flow deliberately does not do. Surfaced as a clear
        # limitation rather than silently ignored.
        return Validated(
            False,
            "adding a new job from natural language is not supported yet -- "
            "create it via POST /jobs, then re-solve",
            None,
            "",
        )

    return Validated(False, f"unsupported change kind {kind!r}", None, "")


def apply_change(
    problem: Problem,
    schedule: Schedule,
    change: DispatchChange,
    now_s: int,
    config: SolverConfig | None = None,
    actuals: tuple[Actual, ...] = (),
) -> tuple[Validated, ReoptResult | None]:
    """Validate then re-optimise. Never writes anything.

    `actuals` are what technicians have reported so far. They are folded into
    the disruption rather than passed separately, because from the solver's
    point of view they are the same kind of thing -- news about the day that
    the morning's schedule did not have. A dispatcher saying "Ahmad is sick"
    and Ahmad's phone saying "I finished at 10:15" are both inputs to the same
    re-plan, and keeping them in one object is what stops one of the two
    callers forgetting to pass the other.
    """
    v = validate(problem, schedule, change, now_s)
    if not v.ok or v.disruption is None:
        return v, None
    disruption = (
        dataclasses.replace(v.disruption, actuals=actuals) if actuals else v.disruption
    )
    result = reoptimise(problem, schedule, disruption, config or SolverConfig())

    # A change can pass validation -- it is well-formed and refers to real
    # people and real jobs -- and still leave the day unschedulable. Extending
    # a job already under way is the usual case: nothing is wrong with the
    # request, it simply does not fit any more.
    #
    # Surfaced as a REFUSAL rather than a preview, because a schedule the
    # solver could not produce is not something to offer a dispatcher an
    # "Apply" button for. The distinction matters: this is "that will not
    # work", not "here is what it costs".
    unsolvable = [x for x in result.violations if x.code is Code.NO_SOLUTION]
    if unsolvable:
        return (
            dataclasses.replace(
                v,
                ok=False,
                reason=(
                    f"{v.description} leaves no workable schedule for the rest "
                    f"of the day ({unsolvable[0].message})"
                ),
            ),
            None,
        )
    return v, result
