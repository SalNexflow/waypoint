"""Status derivation and clock sanitising.

Pure functions, no database and no HTTP:

    docker compose exec api python -m pytest tests/test_field_status.py

The endpoint's behaviour -- idempotency, scoping, the jobs.status cache --
is tested over HTTP in tests/test_api.py, because what matters there is that
the whole chain agrees.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.field_status import MAX_BACKDATE, clamp_occurred_at, highest_status
from api.tables import FIELD_STATUSES, FIELD_STATUS_RANK


NOW = datetime(2026, 9, 3, 11, 40, tzinfo=UTC)


# --- Rank -------------------------------------------------------------------


def test_every_status_has_a_rank():
    """A status the ranking does not know about would silently rank 0 and
    could never become a job's current state."""
    assert set(FIELD_STATUSES) == set(FIELD_STATUS_RANK)


def test_ranks_are_strictly_increasing_in_the_order_work_happens():
    ranks = [FIELD_STATUS_RANK[s] for s in ("en_route", "arrived", "complete")]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == 3


# --- Highest status ---------------------------------------------------------


def test_no_events_means_no_status():
    assert highest_status([]) is None


@pytest.mark.parametrize("status", FIELD_STATUSES)
def test_a_single_event_is_its_own_status(status):
    assert highest_status([status]) == status


def test_the_furthest_along_status_wins_regardless_of_order():
    """THE out-of-order guarantee, stated directly.

    A queued `en_route` that syncs after a `complete` -- which is exactly what
    a phone coming out of a dead zone does -- must not demote the job. The
    derivation asks "which of these got furthest", not "which arrived last",
    so arrival order cannot affect the answer.
    """
    assert highest_status(["complete", "en_route"]) == "complete"
    assert highest_status(["en_route", "complete"]) == "complete"
    assert highest_status(["complete", "arrived", "en_route"]) == "complete"
    assert highest_status(["en_route", "arrived"]) == "arrived"


def test_an_unknown_status_never_outranks_a_real_one():
    """A value written by a newer version of the app must not take over."""
    assert highest_status(["arrived", "teleported"]) == "arrived"
    assert highest_status(["teleported"]) is None


def test_duplicates_change_nothing():
    assert highest_status(["arrived", "arrived", "arrived"]) == "arrived"


# --- Clock sanitising -------------------------------------------------------


def test_a_believable_time_is_left_alone():
    claimed = NOW - timedelta(minutes=85)
    assert clamp_occurred_at(claimed, NOW) == claimed


def test_a_time_in_the_future_is_impossible():
    """It cannot have happened after we heard about it.

    A phone running fast is the common cause, and the honest correction is
    "no earlier than now" rather than trusting a number that describes an
    event that has not occurred.
    """
    assert clamp_occurred_at(NOW + timedelta(hours=3), NOW) == NOW


def test_a_time_far_in_the_past_is_a_broken_clock_not_a_dead_zone():
    """A shift is under twelve hours and the longest believable offline
    stretch is one working day. Three days back is a clock that was never
    set, so the value is pulled to the edge of the band rather than trusted.
    """
    assert clamp_occurred_at(NOW - timedelta(days=3), NOW) == NOW - MAX_BACKDATE


def test_the_edge_of_the_band_is_kept():
    edge = NOW - MAX_BACKDATE
    assert clamp_occurred_at(edge, NOW) == edge


def test_clamping_is_visible_by_comparison():
    """Nothing is hidden: the raw value is stored alongside, so
    `occurred_at != client_occurred_at` is how a reader knows the timestamp
    was not trusted. These are the two cases where that flag fires."""
    assert clamp_occurred_at(NOW + timedelta(seconds=1), NOW) != NOW + timedelta(
        seconds=1
    )
    too_old = NOW - MAX_BACKDATE - timedelta(seconds=1)
    assert clamp_occurred_at(too_old, NOW) != too_old
