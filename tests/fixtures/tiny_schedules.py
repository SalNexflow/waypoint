"""Four hand-written schedules for the tiny instance. Phase 5 exercise.

One of these is valid. Three contain exactly one violation each. They are not
labelled, and the ordering is not a hint.

Every timestamp was computed by hand against the frozen matrix in
tiny_matrix.py, not produced by a solver. That is the point: your checker has
to be right about schedules whose correctness was established without any of
the code it might share a bug with.

The three broken ones are deliberately subtle. Each is internally consistent
and looks entirely reasonable printed out -- no job at 3am, no technician in
two places by an hour, nothing that catches the eye. Two of them are wrong by
single-digit minutes.

When your checker is written, run:

    docker compose exec api python -m tests.fixtures.tiny_schedules

It will tell you which verdicts you got right. It reveals what each violation
actually was only once all four verdicts are correct -- getting the verdict
right by luck and the reason wrong is the failure mode worth catching.

Times are seconds since local midnight on 2026-09-03.
"""

from __future__ import annotations

from solver.solution import Schedule, Visit
from tests.fixtures.tiny_matrix import problem as tiny_problem

DAY = __import__("datetime").date(2026, 9, 3)


def _v(job, tech, seq, arrive, start, dur_min):
    return Visit(
        job_ref=job,
        technician_ref=tech,
        sequence=seq,
        arrive_s=arrive,
        start_s=start,
        end_s=start + dur_min * 60,
    )


# ---------------------------------------------------------------------------

SCHEDULE_1 = Schedule(
    day=DAY,
    visits=(
        _v("J1", "T1", 0, 29210, 29210, 60),
        _v("J2", "T1", 1, 32957, 32957, 60),
        _v("J4", "T1", 2, 37150, 37150, 60),
        _v("J3", "T2", 0, 28999, 32400, 90),
        _v("J5", "T2", 1, 38836, 43200, 60),
    ),
    meta={"origin": "hand-written fixture"},
)

SCHEDULE_2 = Schedule(
    day=DAY,
    visits=(
        _v("J1", "T1", 0, 29210, 29210, 60),
        _v("J2", "T1", 1, 33077, 33077, 60),
        _v("J4", "T1", 2, 37270, 37270, 60),
        _v("J3", "T2", 0, 28999, 32400, 90),
        _v("J5", "T2", 1, 38836, 43200, 60),
    ),
    meta={"origin": "hand-written fixture"},
)

SCHEDULE_3 = Schedule(
    day=DAY,
    visits=(
        _v("J2", "T1", 0, 29207, 35940, 60),
        _v("J1", "T1", 1, 39840, 39840, 60),
        _v("J4", "T1", 2, 44109, 44109, 60),
        _v("J3", "T2", 0, 28999, 32400, 90),
        _v("J5", "T2", 1, 38836, 43200, 60),
    ),
    meta={"origin": "hand-written fixture"},
)

SCHEDULE_4 = Schedule(
    day=DAY,
    visits=(
        _v("J1", "T1", 0, 29210, 29210, 60),
        _v("J3", "T1", 1, 34100, 34100, 90),
        _v("J4", "T1", 2, 40331, 40331, 60),
        _v("J2", "T2", 0, 30105, 32400, 60),
        _v("J5", "T2", 1, 36894, 43200, 60),
    ),
    meta={"origin": "hand-written fixture"},
)

SCHEDULES = {
    "SCHEDULE_1": SCHEDULE_1,
    "SCHEDULE_2": SCHEDULE_2,
    "SCHEDULE_3": SCHEDULE_3,
    "SCHEDULE_4": SCHEDULE_4,
}


# --- Grading ---------------------------------------------------------------
# The answers, kept together and away from the schedules above so they are not
# read by accident while scrolling.

_ANSWERS: dict[str, tuple[bool, str, str]] = {
    "SCHEDULE_1": (
        False,
        "travel time not respected",
        "T1 leaves J1 at 09:06:50 and is recorded arriving at J2 at 09:09:17. "
        "That leg takes 267s; the schedule allows 147s. Short by exactly "
        "2 minutes. Everything else about this schedule is correct.",
    ),
    "SCHEDULE_2": (
        True,
        "valid",
        "No violations. Note the two long waits on T2: arriving at 08:03 for "
        "a 09:00 window, and at 10:47 for a 12:00 window. Waiting is legal, "
        "and a checker that assumed arrival == start would wrongly reject "
        "this one.",
    ),
    "SCHEDULE_3": (
        False,
        "hard time window violated",
        "J1's window closes at 12:00 and the job takes 60 minutes, so the "
        "latest legal start is 11:00. This schedule starts it at 11:04 and "
        "finishes at 12:04. Late by exactly 4 minutes. The travel times and "
        "every other window in this schedule are correct.",
    ),
    "SCHEDULE_4": (
        False,
        "technician not eligible for the job",
        "J3 needs skills {chiller, refrigerant_handling} and part gas_r410a. "
        "It is assigned to T1 (Ahmad), who has {electrical, split_unit} and "
        "carries {contactor, filter_set}. Every timestamp in this schedule is "
        "arithmetically perfect, which is what makes it worth catching: "
        "a checker that only verifies times passes it.",
    ),
}


def grade(checker) -> str:
    """Run a phase 5 checker against all four schedules and report.

    `checker` must be a callable taking (problem, schedule) and returning a
    list of violations -- empty list meaning the schedule is valid. Anything
    falsy is treated as "valid".

    Reasons are revealed only once all four verdicts are correct.
    """
    problem = tiny_problem()
    lines: list[str] = []
    correct = 0

    lines.append(f"{'schedule':<14}{'your verdict':<16}{'expected':<12}{'result'}")
    lines.append("-" * 56)

    results = {}
    for name in sorted(SCHEDULES):
        expected_valid = _ANSWERS[name][0]
        try:
            violations = checker(problem, SCHEDULES[name])
        except Exception as exc:  # noqa: BLE001 - report, do not crash the grader
            lines.append(f"{name:<14}{'RAISED':<16}"
                         f"{'valid' if expected_valid else 'invalid':<12}"
                         f"error: {type(exc).__name__}: {exc}")
            results[name] = False
            continue

        said_valid = not violations
        ok = said_valid == expected_valid
        correct += ok
        results[name] = ok
        lines.append(
            f"{name:<14}"
            f"{('valid' if said_valid else 'invalid'):<16}"
            f"{('valid' if expected_valid else 'invalid'):<12}"
            f"{'ok' if ok else 'WRONG'}"
        )
        if violations and isinstance(violations, (list, tuple)):
            for v in list(violations)[:3]:
                lines.append(f"{'':<14}  -> {v}")

    lines.append("")
    lines.append(f"{correct}/4 verdicts correct")

    if correct == 4:
        lines.append("")
        lines.append("All verdicts correct. What each schedule actually contained:")
        lines.append("")
        for name in sorted(SCHEDULES):
            _, short, detail = _ANSWERS[name]
            lines.append(f"  {name}: {short}")
            for chunk in _wrap(detail, 68):
                lines.append(f"      {chunk}")
            lines.append("")
        lines.append("Now check your checker reported the RIGHT REASON for each,")
        lines.append("not just the right verdict. A checker that rejects a valid")
        lines.append("schedule for the wrong reason is not finished.")
    else:
        lines.append("")
        lines.append("Reasons stay hidden until all four verdicts are correct.")

    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    words, out, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out


if __name__ == "__main__":
    import importlib
    import sys

    from solver.problem import describe
    from solver.solution import render

    if "--show" in sys.argv:
        prob = tiny_problem()
        print(describe(prob))
        print()
        for name in sorted(SCHEDULES):
            print("=" * 72)
            print(name)
            print("=" * 72)
            print(render(prob, SCHEDULES[name]))
            print()
        raise SystemExit(0)

    try:
        mod = importlib.import_module("solver.check")
    except ModuleNotFoundError:
        print("No solver/check.py yet -- that is phase 5.")
        print()
        print("Write a function taking (problem, schedule) and returning a list")
        print("of violations (empty list = valid), export it as `check`, then")
        print("run this again to be graded.")
        print()
        print("To look at the four schedules first:")
        print("  docker compose exec api python -m tests.fixtures.tiny_schedules --show")
        raise SystemExit(0)

    fn = getattr(mod, "check", None)
    if fn is None:
        print("solver/check.py exists but has no `check` function.")
        raise SystemExit(1)

    print(grade(fn))
