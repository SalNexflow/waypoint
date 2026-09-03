"""Tests for the model, the checker, and the explainer.

The central test in this file is
`test_solver_output_always_passes_the_independent_checker`. Everything else
supports it. A solver that returns a schedule violating a constraint you
modelled wrong will look completely plausible, so the only real defence is
running an independently written checker over its output on every instance
that matters.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from solver.check import Code, check, summarise
from solver.explain import Reason, explain_job, explain_schedule
from solver.model import Pin, SolverConfig, solve
from solver.problem import from_seed_instance
from solver.solution import Schedule, Visit
from tests.fixtures.tiny_matrix import matrix
from tests.fixtures.tiny_matrix import problem as tiny_problem
from tests.fixtures.tiny_schedules import SCHEDULES

DAY = date(2026, 9, 3)


@pytest.fixture
def problem():
    return tiny_problem()


@pytest.fixture
def solved(problem):
    return solve(problem, SolverConfig(time_limit_s=10, workers=1))


# --- Phase 4: feasibility ---------------------------------------------------


def test_solves_the_tiny_instance(problem):
    s = solve(
        problem,
        SolverConfig(time_limit_s=10, use_objective=False, allow_unassigned=False),
    )
    assert s.meta["status"] in ("OPTIMAL", "FEASIBLE")
    assert len(s.visits) == 5
    assert not s.unassigned


def test_forced_assignments_land_on_the_only_eligible_technician(solved):
    """J3 needs chiller, J4 needs electrical. Nothing else can do them."""
    assert solved.visit_for("J3").technician_ref == "T2"
    assert solved.visit_for("J4").technician_ref == "T1"


def test_every_visit_respects_its_hard_window(solved, problem):
    for v in solved.visits:
        job = problem.job(v.job_ref)
        assert v.start_s >= job.hard_start_s
        assert v.end_s <= job.hard_end_s


def test_sequences_are_contiguous_per_technician(solved):
    for visits in solved.by_technician().values():
        assert [v.sequence for v in visits] == list(range(len(visits)))


def test_no_technician_exceeds_their_shift(solved, problem):
    for tech_ref, visits in solved.by_technician().items():
        t = problem.tech(tech_ref)
        for v in visits:
            assert v.end_s <= t.shift_end_s
            assert v.arrive_s >= t.shift_start_s


def test_travel_time_is_respected_between_consecutive_jobs(solved, problem):
    for tech_ref, visits in solved.by_technician().items():
        node = problem.tech(tech_ref).node
        clock = problem.tech(tech_ref).shift_start_s
        for v in visits:
            job = problem.job(v.job_ref)
            assert v.arrive_s - clock >= problem.travel_s(node, job.node)
            node, clock = job.node, v.end_s


def test_solver_is_deterministic_with_one_worker(problem):
    cfg = SolverConfig(time_limit_s=10, workers=1)
    a, b = solve(problem, cfg), solve(problem, cfg)
    assert [dataclasses.astuple(v) for v in a.visits] == [
        dataclasses.astuple(v) for v in b.visits
    ]


def test_meta_reports_matrix_provenance(solved):
    assert solved.meta["matrix_source"] == "osrm"
    assert solved.meta["reportable"] is True


# --- The cross-check --------------------------------------------------------


def test_solver_output_passes_the_checker_on_the_tiny_instance(solved, problem):
    assert check(problem, solved) == []


@pytest.mark.parametrize("seed", [1, 7, 42])
@pytest.mark.parametrize("n_jobs,n_techs", [(6, 2), (14, 4)])
def test_solver_output_always_passes_the_independent_checker(seed, n_jobs, n_techs):
    """The test this whole project's credibility rests on.

    Uses the haversine provider so it stays hermetic -- the point here is
    constraint correctness, which does not depend on the matrix being real,
    only on it being consistent.
    """
    import asyncio

    from data.seed.generate import generate_instance
    from routing.haversine import HaversineProvider
    from solver.problem import seed_coords

    inst = generate_instance(
        n_jobs=n_jobs, n_technicians=n_techs, day=DAY, seed=seed
    )
    m = asyncio.run(HaversineProvider().matrix(seed_coords(inst)))
    prob = from_seed_instance(inst, m)
    sched = solve(prob, SolverConfig(time_limit_s=8, workers=1))

    violations = check(prob, sched)
    assert violations == [], summarise(violations)


def test_infeasible_problem_returns_a_schedule_not_an_exception(problem):
    """Over-constrain it and confirm we still get something to explain."""
    s = solve(
        problem,
        SolverConfig(time_limit_s=5, allow_unassigned=False),
        pins=[Pin("J3", "T2", start_s=problem.job("J3").hard_start_s)],
    )
    assert isinstance(s, Schedule)


def test_pinning_a_job_to_an_ineligible_technician_is_reported(problem):
    s = solve(problem, SolverConfig(time_limit_s=5), pins=[Pin("J3", "T1")])
    assert s.meta["status"] == "MODEL_ERROR"
    assert "not eligible" in s.meta["reason"]


def test_pin_holds_the_job_on_its_technician(problem):
    s = solve(problem, SolverConfig(time_limit_s=10), pins=[Pin("J1", "T2")])
    assert s.visit_for("J1").technician_ref == "T2"
    assert check(problem, s) == []


def test_pin_with_a_start_time_holds_the_time(problem):
    at = 10 * 3600
    s = solve(problem, SolverConfig(time_limit_s=10), pins=[Pin("J2", "T1", at)])
    assert s.visit_for("J2").start_s == at


def test_require_forces_a_job_to_be_assigned(problem):
    s = solve(problem, SolverConfig(time_limit_s=10), require=["J5"])
    assert "J5" not in s.unassigned


# --- Phase 5: the checker ---------------------------------------------------


def test_checker_accepts_the_valid_fixture(problem):
    assert check(problem, SCHEDULES["SCHEDULE_2"]) == []


def test_checker_catches_travel_violation(problem):
    v = check(problem, SCHEDULES["SCHEDULE_1"])
    assert [x.code for x in v] == [Code.TRAVEL_TIME_VIOLATED]
    assert v[0].magnitude_s == 120  # exactly two minutes short


def test_checker_catches_window_violation(problem):
    v = check(problem, SCHEDULES["SCHEDULE_3"])
    assert [x.code for x in v] == [Code.WINDOW_LATE]
    assert v[0].magnitude_s == 240  # exactly four minutes late


def test_checker_catches_eligibility_violation(problem):
    codes = {x.code for x in check(problem, SCHEDULES["SCHEDULE_4"])}
    assert Code.SKILL_MISMATCH in codes
    assert Code.PART_MISSING in codes


def test_checker_allows_waiting(problem):
    """Arriving before a window opens is legal. A checker that assumed
    arrival == start would reject the valid fixture, which has two long waits."""
    waits = [v.wait_s for v in SCHEDULES["SCHEDULE_2"].visits if v.wait_s > 0]
    assert len(waits) == 2
    assert check(problem, SCHEDULES["SCHEDULE_2"]) == []


def test_checker_catches_a_missing_job(problem):
    partial = Schedule(day=DAY, visits=SCHEDULES["SCHEDULE_2"].visits[:3])
    codes = {v.code for v in check(problem, partial)}
    assert Code.MISSING_JOB in codes


def test_checker_accepts_declared_unassigned(problem):
    s = Schedule(
        day=DAY,
        visits=tuple(
            v for v in SCHEDULES["SCHEDULE_2"].visits if v.job_ref not in ("J5",)
        ),
        unassigned=("J5",),
    )
    assert [v.code for v in check(problem, s)] == []


def test_checker_catches_duplicate_assignment(problem):
    base = SCHEDULES["SCHEDULE_2"]
    dup = Visit("J1", "T2", 2, 50000, 50000, 53600)
    codes = {
        v.code
        for v in check(problem, Schedule(day=DAY, visits=(*base.visits, dup)))
    }
    assert Code.DUPLICATE_ASSIGNMENT in codes


def test_checker_catches_a_job_both_assigned_and_unassigned(problem):
    s = Schedule(day=DAY, visits=SCHEDULES["SCHEDULE_2"].visits, unassigned=("J1",))
    codes = {v.code for v in check(problem, s)}
    assert Code.ASSIGNED_AND_UNASSIGNED in codes


def test_checker_catches_bad_sequence_numbers(problem):
    bad = tuple(
        dataclasses.replace(v, sequence=9) if v.job_ref == "J2" else v
        for v in SCHEDULES["SCHEDULE_2"].visits
    )
    codes = {v.code for v in check(problem, Schedule(day=DAY, visits=bad))}
    assert Code.BAD_SEQUENCE in codes


def test_checker_catches_unknown_refs(problem):
    s = Schedule(day=DAY, visits=(Visit("J99", "T9", 0, 0, 0, 60),), unassigned=())
    codes = {v.code for v in check(problem, s)}
    assert Code.UNKNOWN_JOB in codes
    assert Code.UNKNOWN_TECHNICIAN in codes


def test_checker_catches_duration_tampering(problem):
    bad = tuple(
        dataclasses.replace(v, end_s=v.end_s - 600) if v.job_ref == "J1" else v
        for v in SCHEDULES["SCHEDULE_2"].visits
    )
    codes = {v.code for v in check(problem, Schedule(day=DAY, visits=bad))}
    assert Code.DURATION_MISMATCH in codes


def test_checker_catches_start_before_arrival(problem):
    bad = tuple(
        dataclasses.replace(v, start_s=v.arrive_s - 60, end_s=v.arrive_s + 3540)
        if v.job_ref == "J4"
        else v
        for v in SCHEDULES["SCHEDULE_2"].visits
    )
    codes = {v.code for v in check(problem, Schedule(day=DAY, visits=bad))}
    assert Code.STARTS_BEFORE_ARRIVAL in codes


def test_checker_reports_every_violation_not_just_the_first(problem):
    """Three separate faults should produce three findings."""
    bad = (
        Visit("J3", "T1", 0, 29210, 29210, 29210 + 5400),   # eligibility
        Visit("J1", "T1", 1, 29210, 29210, 29210 + 3600),   # travel + overlap
    )
    v = check(problem, Schedule(day=DAY, visits=bad, unassigned=("J2", "J4", "J5")))
    assert len({x.code for x in v}) >= 3


def test_summarise_reads_clearly(problem):
    assert "VALID" in summarise([])
    assert "INVALID" in summarise(check(problem, SCHEDULES["SCHEDULE_3"]))


# --- Phase 6: objective -----------------------------------------------------


def test_objective_reduces_travel_versus_feasibility_only(problem):
    feas = solve(
        problem, SolverConfig(time_limit_s=10, use_objective=False,
                              allow_unassigned=False)
    )
    opt = solve(
        problem, SolverConfig(time_limit_s=10, use_objective=True,
                              allow_unassigned=False)
    )
    assert opt.meta["travel_s"] <= feas.meta["travel_s"]


def test_unassigned_weight_dominates_travel():
    """The spec contradiction, pinned as a test: the solver must never drop a
    job to save driving."""
    cfg = SolverConfig()
    max_conceivable_travel = 15 * 9 * 3600
    assert cfg.w_unassigned > max_conceivable_travel * cfg.w_travel


def test_overtime_is_forbidden_by_default(problem):
    assert SolverConfig().allowed_overtime_s == 0


def test_allowing_overtime_relaxes_the_shift(problem):
    s = solve(problem, SolverConfig(time_limit_s=10, allowed_overtime_s=3600))
    assert check(problem, s, allowed_overtime_s=3600) == []


def test_config_snapshot_round_trips():
    cfg = SolverConfig(time_limit_s=12.5, w_travel=3)
    d = cfg.as_dict()
    assert d["time_limit_s"] == 12.5
    assert d["w_travel"] == 3


# --- Phase 7: the explainer -------------------------------------------------


def test_explains_a_missing_skill(problem):
    """Remove chiller from everyone and J3 becomes impossible."""
    techs = tuple(
        dataclasses.replace(t, skills=t.skills - {"chiller"})
        for t in problem.technicians
    )
    p2 = dataclasses.replace(problem, technicians=techs)
    s = solve(p2, SolverConfig(time_limit_s=5))
    e = explain_job(p2, s, "J3", probe=False)
    assert e.reason is Reason.NO_SKILL
    assert "chiller" in e.message


def test_explains_a_missing_part(problem):
    techs = tuple(
        dataclasses.replace(t, van_stock={k: v for k, v in t.van_stock.items()
                                          if k != "gas_r410a"})
        for t in problem.technicians
    )
    p2 = dataclasses.replace(problem, technicians=techs)
    s = solve(p2, SolverConfig(time_limit_s=5))
    e = explain_job(p2, s, "J3", probe=False)
    assert e.reason is Reason.NO_PART
    assert "gas_r410a" in e.message


def test_explains_an_unreachable_job(problem):
    """Shrink both shifts to a morning sliver; the 12:00 job cannot be met."""
    techs = tuple(
        dataclasses.replace(t, shift_end_s=10 * 3600) for t in problem.technicians
    )
    p2 = dataclasses.replace(problem, technicians=techs)
    s = solve(p2, SolverConfig(time_limit_s=5))
    e = explain_job(p2, s, "J5", probe=False)
    assert e.reason is Reason.UNREACHABLE


def test_explains_a_window_shorter_than_the_job(problem):
    jobs = tuple(
        dataclasses.replace(j, hard_end_s=j.hard_start_s + 600) if j.ref == "J3" else j
        for j in problem.jobs
    )
    p2 = dataclasses.replace(problem, jobs=jobs)
    s = solve(p2, SolverConfig(time_limit_s=5))
    e = explain_job(p2, s, "J3", probe=False)
    assert e.reason is Reason.WINDOW_TOO_SHORT


def test_explain_schedule_covers_every_unassigned_job(problem):
    techs = tuple(
        dataclasses.replace(t, max_jobs=1) for t in problem.technicians
    )
    p2 = dataclasses.replace(problem, technicians=techs)
    s = solve(p2, SolverConfig(time_limit_s=5))
    assert len(s.unassigned) >= 1
    es = explain_schedule(p2, s, probe=False)
    assert {e.job_ref for e in es} == set(s.unassigned)


def test_fully_assigned_schedule_has_nothing_to_explain(problem, solved):
    if not solved.unassigned:
        assert explain_schedule(problem, solved, probe=False) == []
