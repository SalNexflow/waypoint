"""Tests for the seed generator.

No database and no network: the generator is pure, so these run in
milliseconds. The determinism tests are the important ones -- the phase 10
benchmark's honesty depends entirely on the same seed producing the same day.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date, timedelta

import pytest

from data.seed.catalog import PARTS, SKILLS
from data.seed.generate import (
    DAY_END,
    DAY_START,
    generate_instance,
    load_ratio,
    validate_instance,
)
from data.seed.geography import DISTRICTS, haversine_km

DAY = date(2026, 9, 3)


@pytest.fixture
def instance():
    return generate_instance(n_jobs=40, n_technicians=8, day=DAY, seed=42)


# --- Determinism ------------------------------------------------------------


def test_same_seed_produces_identical_instance():
    a = generate_instance(n_jobs=40, n_technicians=8, day=DAY, seed=42)
    b = generate_instance(n_jobs=40, n_technicians=8, day=DAY, seed=42)
    # Dataclass __eq__ compares field by field, all the way down.
    assert a == b


def test_different_seed_produces_different_instance():
    a = generate_instance(n_jobs=40, n_technicians=8, day=DAY, seed=42)
    b = generate_instance(n_jobs=40, n_technicians=8, day=DAY, seed=43)
    assert a != b


def test_determinism_holds_across_processes():
    """The one that catches PYTHONHASHSEED bugs.

    Python randomises string hashing per process, so a set iterated anywhere
    in the generator would make output vary between runs while looking
    perfectly stable inside a single test session. Two subprocesses is the
    only way to see it.
    """
    code = (
        "from datetime import date;"
        "from data.seed.generate import generate_instance;"
        "i = generate_instance(n_jobs=25, n_technicians=6,"
        " day=date(2026,9,3), seed=7);"
        "print([t.skills for t in i.technicians]);"
        "print([(j.ref, j.customer, j.lat, j.lon, j.required_parts)"
        " for j in i.jobs])"
    )
    runs = [
        subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        ).stdout
        for _ in range(2)
    ]
    assert runs[0] == runs[1]


# --- Shape ------------------------------------------------------------------


def test_counts_match_request(instance):
    assert len(instance.jobs) == 40
    assert len(instance.technicians) == 8
    assert len(instance.depots) == 3


def test_orphan_jobs_are_additional():
    inst = generate_instance(n_jobs=20, n_technicians=5, day=DAY, seed=1,
                             orphan_jobs=3)
    assert len(inst.jobs) == 23
    orphans = [j for j in inst.jobs if j.archetype == "orphan"]
    assert len(orphans) == 3


def test_job_refs_are_unique(instance):
    refs = [j.ref for j in instance.jobs]
    assert len(refs) == len(set(refs))


# --- Coverage guarantees ----------------------------------------------------


def test_every_skill_is_held_by_someone(instance):
    held = {s for t in instance.technicians for s in t.skills}
    assert set(SKILLS) <= held


def test_every_part_is_carried_by_someone(instance):
    carried = {p for t in instance.technicians for p in t.van_stock}
    assert set(PARTS) <= carried


def test_coverage_holds_with_fewer_technicians_than_skills():
    """Round-robin dealing must still cover all six skills across two techs."""
    inst = generate_instance(n_jobs=10, n_technicians=2, day=DAY, seed=5)
    held = {s for t in inst.technicians for s in t.skills}
    assert set(SKILLS) <= held


def test_every_technician_has_at_least_one_skill(instance):
    assert all(t.skills for t in instance.technicians)


# --- Time windows -----------------------------------------------------------


def test_hard_window_always_fits_the_job(instance):
    for j in instance.jobs:
        span = (j.hard_window_end - j.hard_window_start).total_seconds()
        assert span >= j.duration_seconds, f"{j.ref} window shorter than duration"


def test_preferred_window_sits_inside_hard_window(instance):
    for j in instance.jobs:
        if j.pref_window_start is None:
            continue
        assert j.pref_window_start >= j.hard_window_start
        assert j.pref_window_end <= j.hard_window_end


def test_windows_stay_inside_the_working_day(instance):
    for j in instance.jobs:
        assert j.hard_window_start.time() >= DAY_START
        assert j.hard_window_end.time() <= DAY_END


def test_windows_are_timezone_aware(instance):
    """Naive datetimes reaching a timestamptz column is a silent 8-hour shift."""
    for j in instance.jobs:
        assert j.hard_window_start.tzinfo is not None
        assert j.hard_window_end.tzinfo is not None


def test_shifts_are_ordered(instance):
    for t in instance.technicians:
        assert t.shift_end > t.shift_start
        assert t.shift_seconds > 0


def test_day_parameter_is_respected():
    other = DAY + timedelta(days=10)
    inst = generate_instance(n_jobs=5, n_technicians=2, day=other, seed=42)
    assert inst.day == other
    assert all(j.hard_window_start.date() == other for j in inst.jobs)


# --- Geography --------------------------------------------------------------


def test_all_coordinates_are_in_the_klang_valley(instance):
    points = [(j.lat, j.lon) for j in instance.jobs]
    points += [(t.home_lat, t.home_lon) for t in instance.technicians]
    for lat, lon in points:
        assert 2.5 <= lat <= 3.6, f"latitude {lat} outside Klang Valley"
        assert 101.2 <= lon <= 102.1, f"longitude {lon} outside Klang Valley"


def test_jobs_are_clustered_not_uniform():
    """Guards the benchmark against flattery.

    Uniformly scattered jobs have no cluster structure, which makes a greedy
    baseline look worse than a real dispatcher and inflates the solver's
    apparent improvement. Real days cluster. If this ever starts failing, the
    generator has drifted toward uniform and every benchmark number after it
    is suspect.
    """
    inst = generate_instance(n_jobs=120, n_technicians=8, day=DAY, seed=11)
    jobs = inst.jobs

    nearest = []
    for i, a in enumerate(jobs):
        d = min(
            haversine_km(a.lat, a.lon, b.lat, b.lon)
            for k, b in enumerate(jobs)
            if k != i
        )
        nearest.append(d)
    mean_nn = sum(nearest) / len(nearest)

    # Uniform over the ~55km Klang Valley box would give a mean
    # nearest-neighbour distance around 2.5km for 120 points. Clustered data
    # comes in far tighter.
    assert mean_nn < 1.2, f"mean nearest-neighbour {mean_nn:.2f}km looks uniform"


def test_district_weights_are_positive():
    for d in DISTRICTS:
        assert d.job_weight > 0 and d.home_weight > 0
        assert d.spread_km > 0


def test_haversine_against_known_distance():
    """KLCC to Petaling Jaya centre is about 12km straight-line."""
    km = haversine_km(3.1578, 101.7117, 3.1073, 101.6067)
    assert 11.0 < km < 13.5, f"got {km:.2f}km"


def test_haversine_is_symmetric_and_zero_at_identity():
    assert haversine_km(3.1, 101.7, 3.1, 101.7) == pytest.approx(0.0)
    a = haversine_km(3.1578, 101.7117, 3.0449, 101.4455)
    b = haversine_km(3.0449, 101.4455, 3.1578, 101.7117)
    assert a == pytest.approx(b)


# --- Instance validation ----------------------------------------------------


def test_generated_instance_passes_its_own_checks(instance):
    assert validate_instance(instance) == []


@pytest.mark.parametrize("seed", [1, 7, 42, 99, 2024])
@pytest.mark.parametrize("n_jobs,n_techs", [(5, 2), (20, 5), (40, 8), (80, 15)])
def test_all_benchmark_sizes_are_valid(seed, n_jobs, n_techs):
    """Every size the phase 10 benchmark will use, across several seeds."""
    inst = generate_instance(
        n_jobs=n_jobs, n_technicians=n_techs, day=DAY, seed=seed
    )
    assert validate_instance(inst) == []


def test_load_ratio_is_plausible(instance):
    assert 0.2 < load_ratio(instance) < 3.0


def test_load_ratio_rises_with_more_jobs():
    light = generate_instance(n_jobs=20, n_technicians=8, day=DAY, seed=3)
    heavy = generate_instance(n_jobs=60, n_technicians=8, day=DAY, seed=3)
    assert load_ratio(heavy) > load_ratio(light)


# --- Input validation -------------------------------------------------------


def test_rejects_zero_jobs():
    with pytest.raises(ValueError):
        generate_instance(n_jobs=0, n_technicians=4, day=DAY, seed=1)


def test_rejects_zero_technicians():
    with pytest.raises(ValueError):
        generate_instance(n_jobs=10, n_technicians=0, day=DAY, seed=1)


def test_rejects_more_technicians_than_names():
    with pytest.raises(ValueError, match="technician names"):
        generate_instance(n_jobs=10, n_technicians=999, day=DAY, seed=1)
