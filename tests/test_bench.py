"""Tests for the baselines and the benchmark harness.

The baselines have to be *correct*, not just fast. A baseline that quietly
violates a constraint would make the comparison meaningless in the flattering
direction, which is the failure this project cares most about avoiding.
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from bench.baseline import BASELINES, cluster_then_nearest_neighbour
from bench.harness import Row, measure
from data.seed.generate import generate_instance
from routing.haversine import HaversineProvider
from solver.check import check, summarise
from solver.greedy import greedy_schedule
from solver.problem import from_seed_instance, seed_coords
from tests.fixtures.tiny_matrix import problem as tiny_problem

DAY = date(2026, 9, 3)


def build(n_jobs: int, n_techs: int, seed: int):
    inst = generate_instance(
        n_jobs=n_jobs, n_technicians=n_techs, day=DAY, seed=seed
    )
    m = asyncio.run(HaversineProvider().matrix(seed_coords(inst)))
    return from_seed_instance(inst, m)


@pytest.mark.parametrize("name", sorted(BASELINES))
@pytest.mark.parametrize("seed", [1, 7, 42])
@pytest.mark.parametrize("n_jobs,n_techs", [(12, 3), (30, 6)])
def test_baselines_produce_valid_schedules(name, seed, n_jobs, n_techs):
    """The one that matters. A cheating baseline flatters the solver."""
    problem = build(n_jobs, n_techs, seed)
    schedule = BASELINES[name](problem)
    violations = check(problem, schedule)
    assert violations == [], summarise(violations)


@pytest.mark.parametrize("name", sorted(BASELINES))
def test_baselines_account_for_every_job(name):
    problem = build(20, 5, 3)
    s = BASELINES[name](problem)
    assert len(s.assigned_refs) + len(s.unassigned) == problem.n_jobs
    assert not (s.assigned_refs & set(s.unassigned))


@pytest.mark.parametrize("name", sorted(BASELINES))
def test_baselines_are_deterministic(name):
    a = BASELINES[name](build(20, 5, 11))
    b = BASELINES[name](build(20, 5, 11))
    assert [v.job_ref for v in a.visits] == [v.job_ref for v in b.visits]


@pytest.mark.parametrize("name", sorted(BASELINES))
def test_baselines_carry_matrix_provenance(name):
    """A haversine-derived baseline must not look reportable."""
    s = BASELINES[name](build(12, 3, 1))
    assert s.meta["reportable"] is False


def test_baselines_assign_something():
    problem = build(20, 5, 1)
    for name, fn in BASELINES.items():
        assert fn(problem).visits, f"{name} assigned nothing"


def test_greedy_is_shared_between_solver_and_bench():
    """The solver warm-starts from the same function the benchmark uses as a
    baseline, so 'the solver improved on it' is a claim about search rather
    than about two different heuristics."""
    assert BASELINES["greedy_nn"] is greedy_schedule


def test_cluster_baseline_uses_every_technician_it_can():
    problem = build(30, 5, 5)
    s = cluster_then_nearest_neighbour(problem)
    used = {v.technician_ref for v in s.visits}
    assert len(used) >= 2


# --- Harness metrics --------------------------------------------------------


def test_measure_counts_assignment_and_validity():
    problem = tiny_problem()
    s = greedy_schedule(problem)
    row = measure(problem, s, "greedy_nn", "tiny", 5)
    assert row.total_jobs == problem.n_jobs
    assert row.assigned == len(s.visits)
    assert row.valid


def test_travel_per_job_is_infinite_when_nothing_was_assigned():
    """Which is exactly why the harness excludes those rows from its means."""
    row = Row("solver 5s", "40j/8t", 0, 40, 0, 0, 0, 5000, True)
    assert row.travel_per_job_s == float("inf")


def test_travel_per_job_normalises():
    row = Row("x", "y", 10, 20, 6000, 0, 8, 100, True)
    assert row.travel_per_job_s == 600


# --- Warm start under an impossible time limit ------------------------------


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_a_timed_out_solve_never_returns_an_empty_day(seed):
    """The regression this pins is a real one, found by the benchmark.

    At 80 jobs / 15 technicians a 5-second solve returned UNKNOWN with zero
    jobs assigned on every seed -- telling a dispatcher that none of their
    work can be done, which is false. Two causes, both fixed:

      * the warm start hinted `visit` and `start` but not the AddCircuit ARC
        literals, so CP-SAT had to solve the routing sub-problem just to make
        the hint usable;
      * a solve that still found nothing returned an empty schedule instead
        of the perfectly good greedy one it started from.

    A 0.1s limit here reproduces the timeout deterministically without a
    90-second test.
    """
    from solver.model import SolverConfig, solve

    problem = build(40, 8, seed)
    schedule = solve(problem, SolverConfig(time_limit_s=0.1, workers=1))

    assert schedule.visits, "a timed-out solve returned an empty day"
    assert not check(problem, schedule), "the fallback must still be valid"
    if schedule.meta["status"] == "UNKNOWN":
        assert schedule.meta["fell_back"] is True, "a fallback must say so"


def test_a_fallback_is_never_credited_as_a_solver_result(monkeypatch):
    """A row that fell back carries greedy's numbers, so the benchmark has to
    mark it. Otherwise 'solver 5s ties greedy' reads as a tie rather than as
    'this size needs more than 5 seconds'."""
    from solver.model import SolverConfig, solve

    problem = build(40, 8, 1)
    schedule = solve(problem, SolverConfig(time_limit_s=0.1, workers=1))
    row = measure(problem, schedule, "solver 0.1s", "40j/8t s1", 100)
    assert row.fell_back == bool(schedule.meta.get("fell_back"))
