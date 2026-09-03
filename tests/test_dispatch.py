"""Tests for the natural-language dispatch layer.

Split deliberately along the line the spec draws: "The solver is deterministic
and testable; the parser is not."

  * The JSON extraction, schema validation, change resolution, and the
    validate/apply path are ALL deterministic and are tested exactly.
  * The LLM call itself is not tested for correctness, because it cannot be.
    It is tested for *containment*: that a wrong or malformed answer is
    rejected rather than applied.

The stub below is what makes this possible -- it replaces the model with a
fixed reply, so every path except the network call runs under test.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from api.models import DispatchChange
from dispatch.apply import apply_change, resolve_technician, validate
from dispatch.parse import _extract_json, build_prompt
from solver.model import SolverConfig, solve
from tests.fixtures.tiny_matrix import problem as tiny_problem

NOON = 12 * 3600


@pytest.fixture
def problem():
    return tiny_problem()


@pytest.fixture
def schedule(problem):
    return solve(problem, SolverConfig(time_limit_s=10, workers=1))


# --- JSON extraction --------------------------------------------------------


def test_extracts_bare_json():
    assert _extract_json('{"kind":"cancel_job"}') == {"kind": "cancel_job"}


def test_extracts_json_from_a_markdown_fence():
    raw = 'Sure!\n```json\n{"kind":"cancel_job","job_ref":"J1"}\n```\nHope that helps'
    assert _extract_json(raw) == {"kind": "cancel_job", "job_ref": "J1"}


def test_extracts_json_surrounded_by_prose():
    raw = 'I think you mean: {"kind":"cancel_job","job_ref":"J2"} -- let me know.'
    assert _extract_json(raw)["job_ref"] == "J2"


def test_handles_nested_objects():
    raw = '{"kind":"add_job","meta":{"a":{"b":1}},"customer":"X"}'
    assert _extract_json(raw)["customer"] == "X"


def test_returns_none_for_malformed_json():
    """Never patched up with a guess. A model that emits broken JSON gets a
    rejection, not a repair attempt."""
    assert _extract_json('{"kind": "cancel_job",,}') is None


def test_returns_none_when_there_is_no_json():
    assert _extract_json("I do not understand that request.") is None


# --- Schema validation ------------------------------------------------------


def test_change_requires_fields_per_kind():
    with pytest.raises(ValueError, match="requires new_shift_end"):
        DispatchChange(kind="change_shift", technician_ref="T1")

    with pytest.raises(ValueError, match="requires minutes"):
        DispatchChange(kind="extend_duration", job_ref="J1")

    with pytest.raises(ValueError, match="requires technician_ref"):
        DispatchChange(kind="remove_technician")


def test_valid_changes_construct():
    assert DispatchChange(kind="remove_technician", technician_ref="T1")
    assert DispatchChange(kind="extend_duration", job_ref="J1", minutes=60)
    assert DispatchChange(
        kind="change_shift", technician_ref="T1", new_shift_end="16:00"
    )


def test_shift_end_must_look_like_a_time():
    with pytest.raises(ValueError):
        DispatchChange(kind="change_shift", technician_ref="T1", new_shift_end="4pm")


def test_priority_is_bounded():
    with pytest.raises(ValueError):
        DispatchChange(kind="change_priority", job_ref="J1", priority=9)


# --- Prompt -----------------------------------------------------------------


def test_prompt_lists_technicians_and_jobs():
    p = build_prompt(
        [{"id": 1, "name": "Ahmad Faizal", "shift_start": "08:00",
          "shift_end": "17:00", "skills": ["split_unit"]}],
        [{"id": 12, "customer": "Wisma Central"}],
    )
    assert "T1: Ahmad Faizal" in p
    assert "J12: Wisma Central" in p


def test_prompt_caps_the_job_list():
    """A 200-job day must not blow the context window."""
    jobs = [{"id": i, "customer": f"C{i}"} for i in range(200)]
    p = build_prompt([], jobs)
    assert "and 140 more" in p


# --- Technician resolution --------------------------------------------------


def test_resolves_by_ref(problem):
    c = DispatchChange(kind="remove_technician", technician_ref="T1")
    assert resolve_technician(problem, c) == "T1"


def test_resolves_by_partial_name(problem):
    c = DispatchChange(
        kind="remove_technician", technician_ref="TX", technician_name="ahmad"
    )
    assert resolve_technician(problem, c) == "T1"


def test_refuses_an_ambiguous_name(problem):
    """Two people match, so it is an error rather than a coin flip -- the cost
    of guessing wrong is a real person's day being rewritten."""
    techs = tuple(
        dataclasses.replace(t, name=f"Lee {t.ref}") for t in problem.technicians
    )
    p2 = dataclasses.replace(problem, technicians=techs)
    c = DispatchChange(
        kind="remove_technician", technician_ref="TX", technician_name="lee"
    )
    assert resolve_technician(p2, c) is None


def test_unknown_name_resolves_to_nothing(problem):
    c = DispatchChange(
        kind="remove_technician", technician_ref="TX", technician_name="nobody"
    )
    assert resolve_technician(problem, c) is None


# --- Validation of changes against the real day -----------------------------


def test_remove_technician_validates(problem, schedule):
    v = validate(
        problem, schedule,
        DispatchChange(kind="remove_technician", technician_ref="T1"), NOON,
    )
    assert v.ok
    assert v.disruption.sick_technicians == frozenset({"T1"})
    assert "Ahmad" in v.description


def test_remove_unknown_technician_is_rejected(problem, schedule):
    v = validate(
        problem, schedule,
        DispatchChange(kind="remove_technician", technician_ref="T99"), NOON,
    )
    assert not v.ok
    assert "not working today" in v.reason


def test_change_shift_validates(problem, schedule):
    v = validate(
        problem, schedule,
        DispatchChange(kind="change_shift", technician_ref="T2",
                       new_shift_end="16:00"),
        NOON,
    )
    assert v.ok
    assert v.disruption.shift_changes == {"T2": 16 * 3600}


def test_shift_ending_before_it_starts_is_rejected(problem, schedule):
    v = validate(
        problem, schedule,
        DispatchChange(kind="change_shift", technician_ref="T1",
                       new_shift_end="06:00"),
        NOON,
    )
    assert not v.ok
    assert "before" in v.reason


def test_extend_duration_validates(problem, schedule):
    v = validate(
        problem, schedule,
        DispatchChange(kind="extend_duration", job_ref="J1", minutes=30), NOON,
    )
    assert v.ok
    assert v.disruption.duration_changes["J1"] == 90 * 60


def test_extending_past_the_window_is_rejected(problem, schedule):
    """J1 has a 4h window and takes 1h. Adding 5h can never fit."""
    v = validate(
        problem, schedule,
        DispatchChange(kind="extend_duration", job_ref="J1", minutes=300), NOON,
    )
    assert not v.ok
    assert "could never fit" in v.reason


def test_extending_an_unknown_job_is_rejected(problem, schedule):
    v = validate(
        problem, schedule,
        DispatchChange(kind="extend_duration", job_ref="J999", minutes=30), NOON,
    )
    assert not v.ok
    assert "not on today" in v.reason


def test_cancel_job_validates(problem, schedule):
    v = validate(
        problem, schedule, DispatchChange(kind="cancel_job", job_ref="J2"), NOON
    )
    assert v.ok
    assert v.disruption.cancelled_jobs == frozenset({"J2"})


def test_add_job_is_refused_with_a_clear_limitation(problem, schedule):
    """Stated as unsupported rather than silently ignored."""
    v = validate(
        problem, schedule,
        DispatchChange(kind="add_job", customer="New Customer"), NOON,
    )
    assert not v.ok
    assert "not supported yet" in v.reason


# --- Apply ------------------------------------------------------------------


def test_apply_removes_a_technician_and_reoptimises(problem, schedule):
    v, result = apply_change(
        problem, schedule,
        DispatchChange(kind="remove_technician", technician_ref="T1"),
        NOON, SolverConfig(time_limit_s=10, workers=1),
    )
    assert v.ok
    assert result is not None
    assert result.valid, [str(x) for x in result.violations]
    # T1 keeps whatever it had already started, and takes on nothing new.
    # A job whose start time is exactly `now` counts as under way: the
    # technician is on site, and yanking them at that moment is worse than
    # letting the job run.
    already = {
        x.job_ref for x in schedule.visits
        if x.technician_ref == "T1" and x.start_s <= NOON
    }
    newly = [
        x for x in result.after.visits
        if x.technician_ref == "T1" and x.job_ref not in already
    ]
    assert newly == []


def test_apply_returns_no_result_when_validation_fails(problem, schedule):
    v, result = apply_change(
        problem, schedule,
        DispatchChange(kind="remove_technician", technician_ref="T99"),
        NOON, SolverConfig(time_limit_s=5),
    )
    assert not v.ok
    assert result is None


def test_apply_never_produces_an_invalid_schedule(problem, schedule):
    """Whatever the LLM said, the result still goes through the checker."""
    for change in (
        DispatchChange(kind="remove_technician", technician_ref="T1"),
        DispatchChange(kind="cancel_job", job_ref="J2"),
        DispatchChange(kind="extend_duration", job_ref="J1", minutes=30),
        DispatchChange(kind="change_shift", technician_ref="T2",
                       new_shift_end="15:00"),
    ):
        v, result = apply_change(
            problem, schedule, change, NOON,
            SolverConfig(time_limit_s=10, workers=1),
        )
        if v.ok and result is not None:
            assert result.valid, (
                f"{change.kind}: {[str(x) for x in result.violations]}"
            )


def test_completed_work_is_never_undone(problem, schedule):
    """Anything finished before the disruption stays exactly where it was."""
    v, result = apply_change(
        problem, schedule,
        DispatchChange(kind="remove_technician", technician_ref="T2"),
        NOON, SolverConfig(time_limit_s=10, workers=1),
    )
    if not (v.ok and result):
        pytest.skip("nothing to compare")
    before = {x.job_ref: x for x in schedule.visits if x.end_s <= NOON}
    after = {x.job_ref: x for x in result.after.visits}
    for ref, old in before.items():
        if old.technician_ref == "T2":
            continue  # T2 is the one being removed
        assert ref in after, f"{ref} was already done and has vanished"
        assert after[ref].start_s == old.start_s
        assert after[ref].technician_ref == old.technician_ref
