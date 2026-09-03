"""Tests for the phase 4 scaffolding: Problem, Schedule, and the fixtures.

Nothing here tests a constraint model -- that does not exist yet. These verify
the *inputs and fixtures* are sound, so that when the model misbehaves you can
rule out the setup.
"""

from __future__ import annotations

import pytest

from solver.problem import build_problem, describe, hhmm, seconds_since_midnight
from solver.solution import Schedule, Visit, render
from tests.fixtures.tiny_matrix import DURATIONS, matrix
from tests.fixtures.tiny_matrix import problem as tiny_problem
from tests.fixtures.tiny_schedules import SCHEDULES


@pytest.fixture
def problem():
    return tiny_problem()


# --- Time conversion --------------------------------------------------------


def test_seconds_since_midnight():
    from datetime import time

    assert seconds_since_midnight(time(0, 0)) == 0
    assert seconds_since_midnight(time(8, 0)) == 28800
    assert seconds_since_midnight(time(17, 30)) == 63000


def test_hhmm_round_trips():
    assert hhmm(28800) == "08:00"
    assert hhmm(43200) == "12:00"
    assert hhmm(0) == "00:00"


# --- Problem structure ------------------------------------------------------


def test_node_layout_is_technicians_then_jobs(problem):
    for i, t in enumerate(problem.technicians):
        assert t.node == i
    for i, j in enumerate(problem.jobs):
        assert j.node == problem.n_techs + i


def test_matrix_size_matches_node_count(problem):
    assert problem.travel.size == problem.n_techs + problem.n_jobs


def test_build_problem_rejects_mismatched_matrix():
    """The check that catches a node index silently out of step with the
    matrix -- a bug that otherwise produces a model that solves and means
    nothing."""
    from solver import tiny

    with pytest.raises(ValueError, match="travel matrix has"):
        build_problem(
            day=tiny.DAY,
            timezone=tiny.TIMEZONE,
            technicians=list(tiny.TECHNICIANS),
            jobs=list(tiny.JOBS)[:3],  # fewer jobs than the matrix covers
            travel=matrix(),
        )


def test_travel_matrix_is_asymmetric(problem):
    """Proof the fixture is a real road network, not a distance formula."""
    diffs = [
        (i, j)
        for i in range(problem.travel.size)
        for j in range(problem.travel.size)
        if i != j and problem.travel_s(i, j) != problem.travel_s(j, i)
    ]
    assert len(diffs) > 20


def test_travel_diagonal_is_zero(problem):
    for i in range(problem.travel.size):
        assert problem.travel_s(i, i) == 0


def test_frozen_matrix_matches_declared_shape():
    assert len(DURATIONS) == 7
    assert all(len(r) == 7 for r in DURATIONS)


# --- Eligibility ------------------------------------------------------------


def test_j3_is_exclusive_to_t2(problem):
    """The chiller job: skills and part both point at Siti only."""
    assert [t.ref for t in problem.eligible(problem.job("J3"))] == ["T2"]


def test_j4_is_exclusive_to_t1(problem):
    """The electrical job: Ahmad only."""
    assert [t.ref for t in problem.eligible(problem.job("J4"))] == ["T1"]


@pytest.mark.parametrize("ref", ["J1", "J2", "J5"])
def test_shared_jobs_are_open_to_both(problem, ref):
    assert len(problem.eligible(problem.job(ref))) == 2


def test_every_job_has_at_least_one_eligible_technician(problem):
    """If this fails the instance is unsolvable for reasons unrelated to the
    model, and phase 4 would be debugging the wrong thing."""
    for j in problem.jobs:
        assert problem.eligible(j), f"{j.ref} has nobody"


def test_every_job_is_reachable_by_an_eligible_technician(problem):
    for j in problem.jobs:
        assert any(problem.reachable(t, j) for t in problem.eligible(j)), j.ref


def test_latest_start_respects_duration(problem):
    for j in problem.jobs:
        assert j.latest_start_s == j.hard_end_s - j.duration_s
        assert j.latest_start_s >= j.hard_start_s, f"{j.ref} window shorter than job"


# --- Schedule shape ---------------------------------------------------------


def test_visit_wait_is_start_minus_arrive():
    v = Visit("J1", "T1", 0, arrive_s=1000, start_s=1600, end_s=5200)
    assert v.wait_s == 600


def test_by_technician_sorts_by_sequence():
    s = Schedule(
        day=SCHEDULES["SCHEDULE_2"].day,
        visits=(
            Visit("J2", "T1", 1, 0, 0, 60),
            Visit("J1", "T1", 0, 0, 0, 60),
        ),
    )
    assert [v.job_ref for v in s.by_technician()["T1"]] == ["J1", "J2"]


def test_render_produces_output(problem):
    text = render(problem, SCHEDULES["SCHEDULE_2"])
    assert "Ahmad Faizal" in text
    assert "Totals" in text
    assert "wait" in text  # SCHEDULE_2 has two long waits


def test_describe_produces_output(problem):
    text = describe(problem)
    assert "Travel minutes" in text
    assert "eligible" in text


# --- Fixture integrity ------------------------------------------------------
#
# Deliberately does NOT assert which schedules are valid -- that is the phase 5
# exercise, and asserting it here would leak the answers.


def test_there_are_four_schedules():
    assert len(SCHEDULES) == 4


@pytest.mark.parametrize("name", sorted(SCHEDULES))
def test_every_schedule_covers_every_job_exactly_once(name, problem):
    """All four assign all five jobs. None of the flaws is a missing job, so
    a checker cannot get the right verdict just by counting."""
    s = SCHEDULES[name]
    refs = [v.job_ref for v in s.visits]
    assert sorted(refs) == ["J1", "J2", "J3", "J4", "J5"]
    assert len(refs) == len(set(refs))


@pytest.mark.parametrize("name", sorted(SCHEDULES))
def test_every_schedule_has_consistent_durations(name, problem):
    """end - start always equals the job duration, in all four. So duration
    arithmetic is never the flaw either."""
    for v in SCHEDULES[name].visits:
        assert v.end_s - v.start_s == problem.job(v.job_ref).duration_s


@pytest.mark.parametrize("name", sorted(SCHEDULES))
def test_every_schedule_starts_work_no_earlier_than_arrival(name):
    for v in SCHEDULES[name].visits:
        assert v.start_s >= v.arrive_s


@pytest.mark.parametrize("name", sorted(SCHEDULES))
def test_every_schedule_renders(name, problem):
    assert render(problem, SCHEDULES[name])
