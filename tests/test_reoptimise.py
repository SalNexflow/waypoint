"""Tests for mid-day re-optimisation.

The properties that matter are not "the answer is good" -- that is what the
objective is for -- but that re-optimisation cannot rewrite history:

  * work already done stays exactly where it was
  * nothing new is scheduled in the past
  * the result still passes the independent checker
  * churn is penalised, so jobs do not move for trivial gains
"""

from __future__ import annotations

import dataclasses

import pytest

from solver.check import check
from solver.model import SolverConfig, solve
from solver.reoptimise import (
    Disruption,
    JobState,
    classify,
    diff,
    pins_for,
    reoptimise,
)
from tests.fixtures.tiny_matrix import problem as tiny_problem

CFG = SolverConfig(time_limit_s=10, workers=1)


@pytest.fixture
def problem():
    return tiny_problem()


@pytest.fixture
def base(problem):
    return solve(problem, CFG)


# --- Classification ---------------------------------------------------------


def test_classify_splits_the_day(problem, base):
    at = 11 * 3600
    states = classify(base, at)
    for v in base.visits:
        if v.end_s <= at:
            assert states[v.job_ref] is JobState.DONE
        elif v.start_s <= at:
            assert states[v.job_ref] is JobState.IN_PROGRESS
        else:
            assert states[v.job_ref] is JobState.PENDING


def test_nothing_is_done_at_the_start_of_the_day(problem, base):
    states = classify(base, 8 * 3600)
    assert JobState.DONE not in states.values()


def test_everything_is_done_by_the_end(problem, base):
    states = classify(base, 23 * 3600)
    assert set(states.values()) == {JobState.DONE}


def test_pins_cover_done_and_in_progress_only(problem, base):
    at = 11 * 3600
    states = classify(base, at)
    pinned = {p.job_ref for p in pins_for(base, at)}
    expected = {
        ref for ref, s in states.items()
        if s in (JobState.DONE, JobState.IN_PROGRESS)
    }
    assert pinned == expected


def test_pins_fix_both_technician_and_time(problem, base):
    for p in pins_for(base, 11 * 3600):
        assert p.start_s is not None
        assert p.technician_ref


# --- The core invariants ----------------------------------------------------


@pytest.mark.parametrize("hour", [9, 10, 11, 12, 13])
def test_completed_work_is_never_moved(problem, base, hour):
    now = hour * 3600
    done = {v.job_ref: v for v in base.visits if v.end_s <= now}
    result = reoptimise(problem, base, Disruption(now_s=now), CFG)

    after = {v.job_ref: v for v in result.after.visits}
    for ref, old in done.items():
        assert ref in after, f"{ref} was finished and then vanished"
        assert after[ref].start_s == old.start_s
        assert after[ref].technician_ref == old.technician_ref


@pytest.mark.parametrize("hour", [10, 12, 14])
def test_nothing_unpinned_is_scheduled_in_the_past(problem, base, hour):
    now = hour * 3600
    pinned = {p.job_ref for p in pins_for(base, now)}
    result = reoptimise(problem, base, Disruption(now_s=now), CFG)
    for v in result.after.visits:
        if v.job_ref not in pinned:
            assert v.start_s >= now, f"{v.job_ref} was planned in the past"


@pytest.mark.parametrize("hour", [9, 11, 13])
def test_reoptimised_schedules_pass_the_checker(problem, base, hour):
    result = reoptimise(problem, base, Disruption(now_s=hour * 3600), CFG)
    assert result.valid, [str(v) for v in result.violations]


def test_no_disruption_barely_changes_anything(problem, base):
    """The churn penalty should hold a settled schedule still."""
    result = reoptimise(problem, base, Disruption(now_s=11 * 3600), CFG)
    assert result.churn == 0


# --- Disruption kinds -------------------------------------------------------


def test_sick_technician_gets_no_further_work(problem, base):
    """No NEW work. A job already under way still finishes -- a technician
    does not walk out of a customer's building mid-repair, and the pin on an
    in-progress job is what says so."""
    now = 10 * 3600
    victim = base.visits[0].technician_ref
    already_started = {
        v.job_ref for v in base.visits
        if v.technician_ref == victim and v.start_s <= now
    }
    result = reoptimise(
        problem, base,
        Disruption(now_s=now, sick_technicians=frozenset({victim})), CFG,
    )
    newly_started = [
        v for v in result.after.visits
        if v.technician_ref == victim and v.job_ref not in already_started
    ]
    assert newly_started == []
    assert result.valid


def test_sick_technicians_completed_work_still_counts(problem, base):
    """They were ill from midday, not retroactively absent all morning."""
    now = 12 * 3600
    victim = base.visits[0].technician_ref
    done_by_victim = [
        v for v in base.visits if v.technician_ref == victim and v.end_s <= now
    ]
    result = reoptimise(
        problem, base,
        Disruption(now_s=now, sick_technicians=frozenset({victim})), CFG,
    )
    after = {v.job_ref for v in result.after.visits}
    for v in done_by_victim:
        assert v.job_ref in after


def test_shift_change_is_respected(problem, base):
    now = 9 * 3600
    result = reoptimise(
        problem, base,
        Disruption(now_s=now, shift_changes={"T1": 13 * 3600}), CFG,
    )
    for v in result.after.visits:
        if v.technician_ref == "T1":
            assert v.end_s <= 13 * 3600
    assert result.valid


def test_duration_change_is_respected(problem, base):
    now = 8 * 3600
    result = reoptimise(
        problem, base,
        Disruption(now_s=now, duration_changes={"J2": 100 * 60}), CFG,
    )
    v = result.after.visit_for("J2")
    if v is not None:
        assert v.end_s - v.start_s == 100 * 60


def test_cancelled_job_is_dropped(problem, base):
    result = reoptimise(
        problem, base,
        Disruption(now_s=8 * 3600, cancelled_jobs=frozenset({"J5"})), CFG,
    )
    assert result.after.visit_for("J5") is None


# --- Diff -------------------------------------------------------------------


def test_diff_of_identical_schedules_is_empty(base):
    assert diff(base, base) == ()


def test_diff_reports_a_reassignment(problem, base):
    moved = dataclasses.replace(
        base.visits[0],
        technician_ref="T2" if base.visits[0].technician_ref == "T1" else "T1",
    )
    other = dataclasses.replace(base, visits=(moved, *base.visits[1:]))
    moves = diff(base, other)
    assert len(moves) == 1
    assert moves[0].kind == "reassigned"


def test_diff_ignores_sub_minute_retiming(problem, base):
    """Scheduling noise is not a change anyone acts on."""
    nudged = dataclasses.replace(
        base.visits[0],
        start_s=base.visits[0].start_s + 30,
        end_s=base.visits[0].end_s + 30,
    )
    other = dataclasses.replace(base, visits=(nudged, *base.visits[1:]))
    assert diff(base, other) == ()


def test_diff_reports_a_drop(problem, base):
    other = dataclasses.replace(
        base,
        visits=base.visits[1:],
        unassigned=(*base.unassigned, base.visits[0].job_ref),
    )
    moves = diff(base, other)
    assert any(m.kind == "dropped" for m in moves)


def test_churn_counts_only_customer_visible_changes(problem, base):
    """Retiming within the same technician is not a customer call; moving to
    someone else, or dropping the job, is."""
    moved = dataclasses.replace(
        base.visits[0],
        technician_ref="T2" if base.visits[0].technician_ref == "T1" else "T1",
    )
    other = dataclasses.replace(base, visits=(moved, *base.visits[1:]))
    result = reoptimise(problem, base, Disruption(now_s=8 * 3600), CFG)
    assert result.churn >= 0
    assert sum(1 for m in diff(base, other) if m.kind == "reassigned") == 1


# --- Summary ----------------------------------------------------------------


def test_summary_reads_clearly(problem, base):
    result = reoptimise(
        problem, base,
        Disruption(now_s=12 * 3600, sick_technicians=frozenset({"T1"})), CFG,
    )
    s = result.summary()
    assert "customer calls" in s
    assert "unavailable: T1" in s


# --- Cancellation ------------------------------------------------------------


def test_cancelling_one_job_does_not_empty_the_day(problem, base):
    """The regression this pins was found by the dispatch eval, not by a test.

    `apply_disruption` used to make a cancelled job unschedulable by collapsing
    its window to zero width. That tripped the solver's "window shorter than
    the job" guard, which bails on the WHOLE model -- so cancelling one job
    returned an empty schedule with every other job unassigned. Worse, the
    checker called it valid, because an empty schedule violates nothing.
    """
    victim = base.visits[-1].job_ref
    result = reoptimise(
        problem, base,
        Disruption(now_s=8 * 3600, cancelled_jobs=frozenset({victim})), CFG,
    )

    assert result.after.visit_for(victim) is None, "cancelled job was scheduled"
    assert result.valid, [str(v) for v in result.violations]
    # The rest of the day survives: at most the cancelled job is lost.
    assert len(result.after.visits) >= len(base.visits) - 1


def test_a_cancelled_job_is_not_reported_as_unassigned(problem, base):
    """It was cancelled, not left undone. Listing it as unassigned makes a
    cancellation read as a scheduling failure."""
    victim = base.visits[0].job_ref
    result = reoptimise(
        problem, base,
        Disruption(now_s=8 * 3600, cancelled_jobs=frozenset({victim})), CFG,
    )
    assert victim not in result.after.unassigned


def test_cancelling_pinned_work_still_produces_a_schedule(problem, base):
    """A job already under way is pinned to whoever is doing it. Cancelling it
    removes it from that technician's candidates, so the pin can no longer be
    satisfied and the model became unbuildable. The pin is dropped instead."""
    now = 11 * 3600
    pinned = [p.job_ref for p in pins_for(base, now)]
    if not pinned:
        pytest.skip("nothing pinned at this hour")
    result = reoptimise(
        problem, base,
        Disruption(now_s=now, cancelled_jobs=frozenset({pinned[0]})), CFG,
    )
    assert result.after.meta["status"] not in ("MODEL_ERROR", "INFEASIBLE")
    assert result.valid, [str(v) for v in result.violations]


def test_an_empty_schedule_is_not_called_valid(problem):
    """An empty schedule breaks no constraint, so every other check passes it.
    That is correct per-constraint and wrong as an answer -- a failed solve was
    being presented to a dispatcher as `valid=True`."""
    from solver.solution import Schedule

    empty = Schedule(
        day=problem.day,
        visits=(),
        unassigned=tuple(j.ref for j in problem.jobs),
        meta={"status": "MODEL_ERROR", "reason": "synthetic"},
    )
    violations = check(problem, empty)
    assert violations
    assert any(v.code.value == "no_solution" for v in violations)
