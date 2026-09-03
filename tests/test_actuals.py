"""Re-optimising around what actually happened, rather than what was predicted.

The point of the field app, from the solver's side. Phases 1-8 collect what
technicians report; this is where it changes a schedule.

Three of these are regressions for bugs that only appeared the first time real
completion times were fed in, and all three failed the same way -- an
INFEASIBLE model returning an empty schedule, which arrives looking exactly
like a plan in which every job on the day was dropped.

They are the same mistake wearing three hats: the model's constraints describe
what may be DECIDED, and a fact does not have to satisfy them. A finished job
can have overrun its SLA window, left its technician on site when the schedule
had them elsewhere, and started earlier than the routing data says they could
have arrived. None of that is a scheduling error; all of it is the past.
"""

from __future__ import annotations

import dataclasses

import pytest

from solver.check import check
from solver.model import SolverConfig, solve
from solver.reoptimise import (
    MAX_ACTUAL_DURATION_S,
    MIN_ACTUAL_DURATION_S,
    Actual,
    Disruption,
    JobState,
    apply_disruption,
    classify,
    drift_by_technician,
    durations_from_actuals,
    pins_for,
    reoptimise,
    vet_actuals,
)
from tests.fixtures.tiny_matrix import problem as tiny_problem

CFG = SolverConfig(time_limit_s=10, workers=1)


@pytest.fixture
def problem():
    return tiny_problem()


@pytest.fixture
def base(problem):
    return solve(problem, CFG)


def _visit(schedule, ref):
    return next(v for v in schedule.visits if v.job_ref == ref)


def _first(schedule):
    """The earliest visit, and the technician doing it."""
    return min(schedule.visits, key=lambda v: v.start_s)


# --- What a report means ----------------------------------------------------


def test_state_comes_from_the_furthest_thing_reported():
    assert Actual("J1", "T1").state is JobState.PENDING
    assert Actual("J1", "T1", en_route_s=100).state is JobState.PENDING
    assert Actual("J1", "T1", arrived_s=100).state is JobState.IN_PROGRESS
    assert Actual("J1", "T1", arrived_s=100, completed_s=200).state is JobState.DONE


def test_a_measured_duration_needs_both_ends():
    assert Actual("J1", "T1", arrived_s=100).measured_duration_s is None
    assert Actual("J1", "T1", completed_s=100).measured_duration_s is None


@pytest.mark.parametrize(
    "span",
    [MIN_ACTUAL_DURATION_S - 1, 0, -600, MAX_ACTUAL_DURATION_S + 1],
)
def test_an_impossible_duration_is_discarded_not_clamped(span):
    """The estimate it falls back to is a considered number.

    A clamped nonsense figure would look just as authoritative in the model
    while being invented -- worse than admitting the measurement is unusable.
    """
    a = Actual("J1", "T1", arrived_s=30000, completed_s=30000 + span)
    assert a.measured_duration_s is None


def test_a_clamped_timestamp_says_what_but_not_when():
    """The phase 5 guard, reaching its consumer.

    A phone whose clock the server had to correct still tells us the job
    happened -- which is worth pinning. It does not tell us when with enough
    confidence to move the rest of the afternoon.
    """
    a = Actual("J1", "T1", arrived_s=0, completed_s=3600, trusted=False)
    assert a.state is JobState.DONE
    assert a.measured_duration_s is None
    assert durations_from_actuals((a,)) == {}


# --- Reported beats inferred ------------------------------------------------


def test_a_report_overrides_the_clock(problem, base):
    """Without actuals, "it was due to finish an hour ago" means done. That is
    the guess the field app exists to replace."""
    first = _first(base)
    late = first.end_s + 3600
    at = first.end_s + 60  # the clock says it should be finished

    assert classify(base, at)[first.job_ref] is JobState.DONE

    still_going = (Actual(first.job_ref, first.technician_ref, arrived_s=first.start_s),)
    assert classify(base, at, still_going)[first.job_ref] is JobState.IN_PROGRESS
    assert late  # (kept for readability of the scenario)


def test_we_only_guess_about_people_who_are_not_telling_us(problem, base):
    """REGRESSION. This is what made the first real run INFEASIBLE.

    A job that ran fifty minutes over leaves its technician still on site when
    the schedule had them starting the next one. Time-based inference pins
    that next job as "done at its predicted time" while the actual pins the
    first as "done later" -- two pinned jobs overlapping on one person, which
    is unsatisfiable.

    So for a technician who has reported anything, silence about a job means
    they have not done it. Everyone else keeps the old inference, because for
    them there is still no better information.
    """
    first = _first(base)
    tech = first.technician_ref
    theirs = [v for v in base.visits if v.technician_ref == tech]
    if len(theirs) < 2:
        pytest.skip("technician has only one job in this fixture")

    at = max(v.end_s for v in theirs) + 60  # past everything, by the clock
    reported = (Actual(first.job_ref, tech, arrived_s=first.start_s),)
    states = classify(base, at, reported)

    # The one they reported is what they said.
    assert states[first.job_ref] is JobState.IN_PROGRESS
    # The rest of theirs are NOT assumed done just because the clock passed.
    for v in theirs:
        if v.job_ref != first.job_ref:
            assert states[v.job_ref] is JobState.PENDING

    # Somebody who has reported nothing still gets the inference.
    others = [v for v in base.visits if v.technician_ref != tech]
    for v in others:
        assert states[v.job_ref] is JobState.DONE


def test_no_two_pins_overlap_on_one_technician(problem, base):
    """The invariant behind the regression above, stated directly."""
    first = _first(base)
    tech = first.technician_ref
    at = max(v.end_s for v in base.visits) + 60
    overran = (
        Actual(
            first.job_ref,
            tech,
            arrived_s=first.start_s,
            completed_s=first.start_s + 3 * 3600,
        ),
    )
    pins = pins_for(base, at, overran)

    spans = []
    for pin in pins:
        if pin.technician_ref != tech or pin.start_s is None:
            continue
        job = problem.job(pin.job_ref)
        spans.append((pin.start_s, pin.start_s + job.duration_s))
    spans.sort()
    for (_, end), (start, _) in zip(spans, spans[1:]):
        assert end <= start, f"pins overlap on {tech}: {spans}"


# --- Pinning ----------------------------------------------------------------


def test_a_finished_job_pins_to_when_it_really_happened(problem, base):
    """The line that makes the whole feature work."""
    first = _first(base)
    actual_start = first.start_s + 22 * 60
    reported = (
        Actual(
            first.job_ref,
            first.technician_ref,
            arrived_s=actual_start,
            completed_s=actual_start + 3000,
        ),
    )
    pin = next(
        p for p in pins_for(base, first.end_s + 60, reported) if p.job_ref == first.job_ref
    )
    assert pin.start_s == actual_start


def test_en_route_pins_the_person_and_not_the_clock(problem, base):
    """The gap phase 5 left open, closed with the model that already existed.

    A technician in the van is committed -- handing the job to somebody else
    means two people driving to one address. But they have not arrived, so
    nobody knows when it starts, and inventing a time would be worse than
    letting the solver work it out.
    """
    later = max(base.visits, key=lambda v: v.start_s)
    at = later.start_s - 600  # not started yet, by any measure
    reported = (Actual(later.job_ref, later.technician_ref, en_route_s=at - 300),)

    pin = next(
        p for p in pins_for(base, at, reported) if p.job_ref == later.job_ref
    )
    assert pin.technician_ref == later.technician_ref
    assert pin.start_s is None


def test_an_untrusted_time_still_pins_but_at_the_prediction(problem, base):
    first = _first(base)
    reported = (
        Actual(
            first.job_ref,
            first.technician_ref,
            arrived_s=first.start_s + 4000,
            completed_s=first.start_s + 9000,
            trusted=False,
        ),
    )
    pin = next(
        p for p in pins_for(base, first.end_s + 60, reported) if p.job_ref == first.job_ref
    )
    assert pin.start_s == first.start_s


# --- Durations and windows --------------------------------------------------


def test_a_measurement_replaces_the_estimate(problem, base):
    first = _first(base)
    job = problem.job(first.job_ref)
    measured = job.duration_s + 40 * 60

    disruption = Disruption(
        now_s=first.start_s + measured,
        actuals=(
            Actual(
                first.job_ref,
                first.technician_ref,
                arrived_s=first.start_s,
                completed_s=first.start_s + measured,
            ),
        ),
    )
    changed = apply_disruption(problem, disruption)
    assert changed.job(first.job_ref).duration_s == measured


def test_a_measurement_beats_a_dispatchers_forecast(problem, base):
    """They were predicting; the technician was there."""
    first = _first(base)
    measured = 45 * 60
    disruption = Disruption(
        now_s=first.start_s + measured,
        duration_changes={first.job_ref: 3 * 3600},
        actuals=(
            Actual(
                first.job_ref,
                first.technician_ref,
                arrived_s=first.start_s,
                completed_s=first.start_s + measured,
            ),
        ),
    )
    assert apply_disruption(problem, disruption).job(first.job_ref).duration_s == measured


def test_a_forecast_still_wins_where_there_is_nothing_to_measure(problem, base):
    """A job still in progress has no measurement, so "this will overrun by an
    hour" is the newest information there is."""
    first = _first(base)
    disruption = Disruption(
        now_s=first.start_s + 600,
        duration_changes={first.job_ref: 3 * 3600},
        actuals=(Actual(first.job_ref, first.technician_ref, arrived_s=first.start_s),),
    )
    assert apply_disruption(problem, disruption).job(first.job_ref).duration_s == 3 * 3600


def test_finished_work_is_admissible_even_when_it_broke_its_own_window(
    problem, base
):
    """REGRESSION, and the more dangerous of the two.

    A hard window is an SLA: it says when a job MAY be scheduled, and the
    checker rejects any schedule that breaks one. Reality does not consult it.
    Pin a job at the time it really ran and the model is asked to satisfy a
    constraint the past has already broken -- it cannot, and returns nothing.

    A hard window constrains DECISIONS, and for a finished job there is no
    decision left. The window widens to admit what happened.
    """
    first = _first(base)
    job = problem.job(first.job_ref)
    overran_until = job.hard_end_s + 2 * 3600

    disruption = Disruption(
        now_s=overran_until,
        actuals=(
            Actual(
                first.job_ref,
                first.technician_ref,
                arrived_s=first.start_s,
                completed_s=overran_until,
            ),
        ),
    )
    # Windows are derived FROM THE PINS, not from the raw report: the pins are
    # where the job actually ends up being held, and computing the window from
    # anything else would open one that no longer covers the pin inside it.
    pins = pins_for(base, disruption.now_s, disruption.actuals)
    widened = apply_disruption(problem, disruption, pins).job(first.job_ref)
    assert widened.hard_end_s >= overran_until
    # Widened, never narrowed: a job that ran inside its SLA keeps its window.
    assert widened.hard_start_s <= job.hard_start_s


def test_a_window_is_never_narrowed_by_a_report(problem, base):
    first = _first(base)
    job = problem.job(first.job_ref)
    disruption = Disruption(
        now_s=first.end_s,
        actuals=(
            Actual(
                first.job_ref,
                first.technician_ref,
                arrived_s=first.start_s,
                completed_s=first.end_s,
            ),
        ),
    )
    pins = pins_for(base, disruption.now_s, disruption.actuals)
    after = apply_disruption(problem, disruption, pins).job(first.job_ref)
    assert after.hard_start_s <= job.hard_start_s
    assert after.hard_end_s >= job.hard_end_s


# --- Drift ------------------------------------------------------------------


def test_drift_is_measured_at_the_last_thing_reported(problem, base):
    """"Marks a job done at 10:15 that was scheduled to end at 10:00, the
    system knows the day is running fifteen minutes behind." """
    first = _first(base)
    reported = (
        Actual(
            first.job_ref,
            first.technician_ref,
            arrived_s=first.start_s,
            completed_s=first.end_s + 15 * 60,
        ),
    )
    assert drift_by_technician(base, reported) == {first.technician_ref: 15 * 60}


def test_finishing_early_reads_as_ahead(problem, base):
    first = _first(base)
    reported = (
        Actual(
            first.job_ref,
            first.technician_ref,
            arrived_s=first.start_s,
            completed_s=first.end_s - 10 * 60,
        ),
    )
    assert drift_by_technician(base, reported)[first.technician_ref] == -600


def test_an_untrusted_report_does_not_move_the_day(problem, base):
    first = _first(base)
    reported = (
        Actual(
            first.job_ref,
            first.technician_ref,
            arrived_s=first.start_s,
            completed_s=first.end_s + 4 * 3600,
            trusted=False,
        ),
    )
    assert drift_by_technician(base, reported) == {}


# --- The whole thing --------------------------------------------------------


def test_running_late_shifts_the_rest_of_that_day(problem, base):
    """The definition of done: "completing a job early or late is reflected in
    a subsequent re-optimisation"."""
    first = _first(base)
    tech = first.technician_ref
    theirs = sorted(
        (v for v in base.visits if v.technician_ref == tech), key=lambda v: v.start_s
    )
    if len(theirs) < 2:
        pytest.skip("technician has only one job in this fixture")

    overrun = 50 * 60
    result = reoptimise(
        problem,
        base,
        Disruption(
            now_s=first.end_s + overrun,
            actuals=(
                Actual(
                    first.job_ref,
                    tech,
                    arrived_s=first.start_s,
                    completed_s=first.end_s + overrun,
                ),
            ),
        ),
        CFG,
    )

    # It produced a schedule at all -- the regressions above both failed here.
    assert result.after.visits, result.after.meta.get("status")
    assert result.valid, result.violations
    assert result.drift == {tech: overrun}

    # The job they were on stays where it happened.
    pinned = _visit(result.after, first.job_ref)
    assert pinned.technician_ref == tech
    assert pinned.start_s == first.start_s

    # And nothing still to do is planned in the past.
    for v in result.after.visits:
        if v.job_ref != first.job_ref:
            assert v.start_s >= 0


def test_a_day_nobody_reported_on_behaves_exactly_as_before(problem, base):
    """Actuals are additive. A day with no reports must re-optimise the way it
    did before any of this existed."""
    at = 11 * 3600
    without = reoptimise(problem, base, Disruption(now_s=at), CFG)
    with_empty = reoptimise(problem, base, Disruption(now_s=at, actuals=()), CFG)

    assert [v.job_ref for v in without.after.visits] == [
        v.job_ref for v in with_empty.after.visits
    ]
    assert without.drift == {} == with_empty.drift


def test_the_result_still_passes_the_independent_checker(problem, base):
    first = _first(base)
    result = reoptimise(
        problem,
        base,
        Disruption(
            now_s=first.end_s + 30 * 60,
            actuals=(
                Actual(
                    first.job_ref,
                    first.technician_ref,
                    arrived_s=first.start_s,
                    completed_s=first.end_s + 30 * 60,
                ),
            ),
        ),
        CFG,
    )
    disrupted = apply_disruption(
        problem, dataclasses.replace(result.disruption)
    )
    assert not check(disrupted, result.after), check(disrupted, result.after)


def test_a_pin_is_never_earlier_than_the_schedule_has_it(problem, base):
    """REGRESSION, and the subtlest of the three.

    A start time is not free-standing. The model derives the earliest a
    technician can be anywhere from their shift start and the travel matrix,
    and the current schedule already sits at that boundary. A report placing a
    job even a minute earlier asks the model to accept an arrival it believes
    impossible, and it answers INFEASIBLE -- the first real report tripped
    this by fifty-nine seconds.

    Arriving LATE is a delay the day must absorb and moves the pin. Arriving
    EARLY means beating the routing estimate, which is not spare capacity the
    model can act on.
    """
    first = _first(base)
    too_early = (
        Actual(first.job_ref, first.technician_ref, arrived_s=first.start_s - 900),
    )
    pin = next(
        p
        for p in pins_for(base, first.end_s + 60, too_early)
        if p.job_ref == first.job_ref
    )
    assert pin.start_s == first.start_s

    later = (
        Actual(first.job_ref, first.technician_ref, arrived_s=first.start_s + 900),
    )
    pin = next(
        p for p in pins_for(base, first.end_s + 60, later) if p.job_ref == first.job_ref
    )
    assert pin.start_s == first.start_s + 900


def test_an_early_arrival_still_produces_a_schedule(problem, base):
    """The failure this guards against was total: an empty schedule, which
    arrives looking like a plan in which every job on the day was dropped."""
    first = _first(base)
    result = reoptimise(
        problem,
        base,
        Disruption(
            now_s=first.end_s + 600,
            actuals=(
                Actual(
                    first.job_ref,
                    first.technician_ref,
                    arrived_s=first.start_s - 600,
                    completed_s=first.end_s,
                ),
            ),
        ),
        CFG,
    )
    assert result.after.visits, result.after.meta.get("status")
    assert result.valid, result.violations


# --- Vetting: whose times can be believed -----------------------------------


def test_reports_that_could_all_be_true_are_believed(problem, base):
    first = _first(base)
    reported = (
        Actual(
            first.job_ref,
            first.technician_ref,
            arrived_s=first.start_s,
            completed_s=first.end_s + 30 * 60,
        ),
    )
    vetted = vet_actuals(base, first.end_s + 30 * 60, reported, problem)
    assert all(a.trusted for a in vetted)


def test_a_technician_who_could_not_have_been_in_two_places_is_not_believed(
    problem, base
):
    """REGRESSION, and the most realistic of the four.

    A technician who forgot to report all morning and taps through their jobs
    at 14:51 stamps every one of them with the same minute. Nobody arrived at
    four addresses in one minute, but the event log has no way to know that --
    and pinning them as reported puts two jobs on one person at once, which is
    unsatisfiable.

    The reports still say the work HAPPENED. They stop saying when.
    """
    tech = _first(base).technician_ref
    theirs = sorted(
        (v for v in base.visits if v.technician_ref == tech), key=lambda v: v.sequence
    )
    if len(theirs) < 2:
        pytest.skip("technician has only one job in this fixture")

    same_minute = max(v.end_s for v in theirs) + 60
    catching_up = tuple(
        Actual(v.job_ref, tech, arrived_s=same_minute, completed_s=same_minute + 60)
        for v in theirs
    )
    vetted = vet_actuals(base, same_minute + 120, catching_up, problem)

    assert not any(a.trusted for a in vetted)
    # Still pinned, to the right person, in the right order.
    pins = pins_for(base, same_minute + 120, vetted)
    mine = [p for p in pins if p.technician_ref == tech]
    assert len(mine) == len(theirs)
    assert [p.start_s for p in mine] == [v.start_s for v in theirs]


def test_doubt_is_per_technician(problem, base):
    """One person catching up on paperwork must not cost everybody else their
    real timings."""
    by_tech: dict[str, list] = {}
    for v in base.visits:
        by_tech.setdefault(v.technician_ref, []).append(v)
    busy = [t for t, vs in by_tech.items() if len(vs) >= 2]
    if len(by_tech) < 2 or not busy:
        pytest.skip("fixture has too few technicians")

    muddled = busy[0]
    clean = next(t for t in by_tech if t != muddled)

    same_minute = max(v.end_s for v in base.visits) + 60
    reports = [
        Actual(v.job_ref, muddled, arrived_s=same_minute, completed_s=same_minute + 60)
        for v in by_tech[muddled]
    ]
    good = by_tech[clean][0]
    reports.append(
        Actual(good.job_ref, clean, arrived_s=good.start_s, completed_s=good.end_s)
    )

    vetted = {a.job_ref: a.trusted for a in vet_actuals(base, same_minute + 120, tuple(reports), problem)}
    assert vetted[good.job_ref] is True
    assert all(not vetted[v.job_ref] for v in by_tech[muddled])


def test_an_unbelievable_day_still_produces_a_schedule(problem, base):
    """The point of the whole guard: whatever arrives, a plan comes back."""
    tech = _first(base).technician_ref
    theirs = [v for v in base.visits if v.technician_ref == tech]
    same_minute = max(v.end_s for v in base.visits) + 60
    catching_up = tuple(
        Actual(v.job_ref, tech, arrived_s=same_minute, completed_s=same_minute + 60)
        for v in theirs
    )
    result = reoptimise(
        problem, base, Disruption(now_s=same_minute + 120, actuals=catching_up), CFG
    )
    assert result.after.visits, result.after.meta.get("status")
    assert result.valid, result.violations


def test_an_inferred_pin_never_widens_a_window(problem, base):
    """REGRESSION. Widening must never become an SLA escape hatch.

    A job pinned by INFERENCE -- "it was due to finish an hour ago, so it is
    done" -- is a guess. Widening its window on a guess lets the solver park
    work outside its SLA and call it legal; a committed schedule then stores
    that as fact and the next re-optimisation widens from there, compounding
    one run at a time until the independent checker rejects a day on which
    nobody did anything wrong.
    """
    # Nothing reported at all: every pin here is inferred from the clock.
    at = max(v.end_s for v in base.visits) + 60
    disruption = Disruption(now_s=at)
    pins = pins_for(base, at, ())
    assert pins, "expected the clock to imply some finished work"

    after = apply_disruption(problem, disruption, pins)
    for v in base.visits:
        before_job = problem.job(v.job_ref)
        after_job = after.job(v.job_ref)
        assert after_job.hard_start_s == before_job.hard_start_s
        assert after_job.hard_end_s == before_job.hard_end_s
