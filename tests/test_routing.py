"""Tests for the routing layer.

No OSRM required: everything here exercises the haversine provider, the cache,
and the structural checks. OSRM itself is verified separately and against
reality, by `python -m routing.verify` -- a unit test cannot tell you whether
a duration is *true*, only whether it is well-formed.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from routing.base import Coord, TravelMatrix, TravelTimeProvider, UnroutableError
from routing.cache import CachingProvider, PairCache
from routing.haversine import HaversineProvider, haversine_km

KLCC = Coord(3.1578, 101.7117)
PJ = Coord(3.1073, 101.6067)
KLANG = Coord(3.0449, 101.4455)


def run(coro):
    return asyncio.run(coro)


# --- Coord ------------------------------------------------------------------


def test_coord_is_lat_then_lon():
    """The whole point of the NamedTuple: ordering cannot be misread."""
    c = Coord(3.1578, 101.7117)
    assert c.lat == 3.1578
    assert c.lon == 101.7117
    # Still a tuple, so it unpacks in declaration order.
    lat, lon = c
    assert (lat, lon) == (3.1578, 101.7117)


def test_coord_rounding_is_stable():
    a = Coord(3.15780000001, 101.71170000001).rounded()
    b = Coord(3.1578, 101.7117).rounded()
    assert a == b


# --- Haversine --------------------------------------------------------------


def test_haversine_known_distance():
    """KLCC to PJ centre is about 12km straight-line."""
    assert 11.0 < haversine_km(KLCC.lat, KLCC.lon, PJ.lat, PJ.lon) < 13.5


def test_haversine_zero_and_symmetric():
    assert haversine_km(3.1, 101.7, 3.1, 101.7) == pytest.approx(0.0)
    ab = haversine_km(KLCC.lat, KLCC.lon, KLANG.lat, KLANG.lon)
    ba = haversine_km(KLANG.lat, KLANG.lon, KLCC.lat, KLCC.lon)
    assert ab == pytest.approx(ba)


def test_haversine_provider_matrix_shape():
    m = run(HaversineProvider().matrix([KLCC, PJ, KLANG]))
    assert m.size == 3
    assert len(m.durations) == 3
    assert all(len(row) == 3 for row in m.durations)


def test_haversine_diagonal_is_zero():
    m = run(HaversineProvider().matrix([KLCC, PJ, KLANG]))
    for i in range(3):
        assert m.duration(i, i) == 0
        assert m.distance(i, i) == 0


def test_haversine_matrix_passes_sanity_checks():
    m = run(HaversineProvider().matrix([KLCC, PJ, KLANG]))
    assert m.sanity_problems() == []


def test_haversine_is_not_reportable():
    """The guard that keeps provisional numbers out of the benchmark."""
    m = run(HaversineProvider().matrix([KLCC, PJ]))
    assert m.source == "haversine"
    assert m.is_reportable is False


def test_haversine_applies_detour_factor():
    plain = haversine_km(KLCC.lat, KLCC.lon, PJ.lat, PJ.lon)
    m = run(HaversineProvider(detour_factor=1.35).matrix([KLCC, PJ]))
    assert m.distance(0, 1) == pytest.approx(plain * 1350, rel=0.01)


def test_haversine_rejects_impossible_settings():
    with pytest.raises(ValueError):
        HaversineProvider(speed_kmh=0)
    with pytest.raises(ValueError, match="through walls"):
        HaversineProvider(detour_factor=0.5)


def test_haversine_single_coordinate():
    m = run(HaversineProvider().matrix([KLCC]))
    assert m.durations == ((0,),)


# --- Structural checks ------------------------------------------------------


def _matrix(durations, source="osrm"):
    n = len(durations)
    coords = tuple(Coord(3.0 + i / 100, 101.6 + i / 100) for i in range(n))
    return TravelMatrix(
        coords=coords,
        durations=tuple(tuple(r) for r in durations),
        distances=tuple(tuple(0 for _ in r) for r in durations),
        source=source,
    )


def test_sanity_catches_nonzero_diagonal():
    problems = _matrix([[5, 100], [100, 0]]).sanity_problems()
    assert any("[0][0]" in p for p in problems)


def test_sanity_catches_negative_duration():
    problems = _matrix([[0, -1], [100, 0]]).sanity_problems()
    assert any("negative" in p for p in problems)


def test_sanity_catches_ragged_rows():
    m = TravelMatrix(
        coords=(KLCC, PJ),
        durations=((0, 100), (100,)),
        distances=((0, 0), (0, 0)),
        source="osrm",
    )
    assert any("expected 2" in p for p in m.sanity_problems())


def test_sanity_catches_implausible_duration():
    """A 20-hour drive inside the Klang Valley means bad input."""
    problems = _matrix([[0, 72000], [72000, 0]]).sanity_problems()
    assert any("implausible" in p for p in problems)


def test_sanity_passes_a_good_matrix():
    assert _matrix([[0, 900, 1800], [900, 0, 1200], [1800, 1200, 0]]).sanity_problems() == []


def test_osrm_source_is_reportable():
    assert _matrix([[0]], source="osrm").is_reportable is True


# --- Cache ------------------------------------------------------------------


def test_cache_round_trip():
    c = PairCache()
    c.put(KLCC, PJ, 1500, 16000)
    assert c.get(KLCC, PJ) == (1500, 16000)


def test_cache_is_directional():
    """A -> B and B -> A are separate. Real road networks are asymmetric."""
    c = PairCache()
    c.put(KLCC, PJ, 1500, 16000)
    assert c.get(PJ, KLCC) is None


def test_cache_tolerates_float_noise():
    c = PairCache()
    c.put(Coord(3.15780000001, 101.7117), PJ, 1500, 16000)
    assert c.get(Coord(3.1578, 101.71170000004), PJ) == (1500, 16000)


def test_cache_respects_max_entries():
    c = PairCache(max_entries=1)
    c.put(KLCC, PJ, 1, 1)
    c.put(PJ, KLANG, 2, 2)
    assert len(c) == 1


def test_cache_persists_to_disk(tmp_path):
    path = tmp_path / "routing.json"
    a = PairCache(path)
    a.put(KLCC, PJ, 1500, 16000)
    a.save()

    b = PairCache(path)
    assert b.get(KLCC, PJ) == (1500, 16000)


def test_cache_survives_a_corrupt_file(tmp_path):
    """A bad cache file must cost one OSRM call, not take the app down."""
    path = tmp_path / "routing.json"
    path.write_text("{ this is not json", encoding="utf-8")
    c = PairCache(path)
    assert len(c) == 0
    c.put(KLCC, PJ, 10, 20)
    assert c.get(KLCC, PJ) == (10, 20)


def test_cache_write_is_atomic(tmp_path):
    path = tmp_path / "routing.json"
    c = PairCache(path)
    c.put(KLCC, PJ, 1500, 16000)
    c.save()
    assert not path.with_suffix(".json.tmp").exists()
    assert json.loads(path.read_text(encoding="utf-8"))


# --- CachingProvider --------------------------------------------------------


class CountingProvider:
    """A test double. Note it inherits from nothing -- Protocol conformance
    is structural, so having the right attributes is the whole requirement."""

    name = "counting"
    source = "osrm"  # pretend to be real, to test reportability passthrough

    def __init__(self):
        self.calls = 0

    async def healthy(self) -> bool:
        return True

    async def matrix(self, coords):
        self.calls += 1
        pts = tuple(coords)
        n = len(pts)
        d = tuple(
            tuple(0 if i == j else 600 + 60 * abs(i - j) for j in range(n))
            for i in range(n)
        )
        dist = tuple(tuple(v * 8 for v in row) for row in d)
        return TravelMatrix(coords=pts, durations=d, distances=dist, source="osrm")


def test_test_double_satisfies_the_protocol():
    assert isinstance(CountingProvider(), TravelTimeProvider)


def test_second_identical_request_is_served_from_cache():
    inner = CountingProvider()
    p = CachingProvider(inner, PairCache(), autosave=False)
    coords = [KLCC, PJ, KLANG]

    first = run(p.matrix(coords))
    second = run(p.matrix(coords))

    assert inner.calls == 1, "second request should not have reached the provider"
    assert first.durations == second.durations


def test_subset_request_is_served_from_cache():
    """The property that makes phase 9 re-optimisation cheap: after solving 3
    coordinates, a re-solve over 2 of them needs no new call."""
    inner = CountingProvider()
    p = CachingProvider(inner, PairCache(), autosave=False)

    run(p.matrix([KLCC, PJ, KLANG]))
    run(p.matrix([KLCC, KLANG]))

    assert inner.calls == 1


def test_new_coordinate_triggers_a_fetch():
    inner = CountingProvider()
    p = CachingProvider(inner, PairCache(), autosave=False)

    run(p.matrix([KLCC, PJ]))
    run(p.matrix([KLCC, PJ, KLANG]))

    assert inner.calls == 2


def test_caching_provider_preserves_source():
    """A cached haversine matrix is still haversine, and still unreportable."""
    p = CachingProvider(HaversineProvider(), PairCache(), autosave=False)
    m = run(p.matrix([KLCC, PJ]))
    assert m.source == "haversine"
    assert m.is_reportable is False
    assert p.source == "haversine"


def test_cache_hit_returns_identical_durations():
    """'Same coords, same answer' -- the spec's requirement, asserted."""
    inner = CountingProvider()
    p = CachingProvider(inner, PairCache(), autosave=False)
    coords = [KLCC, PJ, KLANG]
    a = run(p.matrix(coords))
    b = run(p.matrix(coords))
    c = run(p.matrix(coords))
    assert a.durations == b.durations == c.durations
    assert a.distances == b.distances == c.distances


def test_cache_stats_track_hits_and_misses():
    inner = CountingProvider()
    cache = PairCache()
    p = CachingProvider(inner, cache, autosave=False)
    run(p.matrix([KLCC, PJ]))
    run(p.matrix([KLCC, PJ]))
    assert cache.stats["hits"] > 0
    assert cache.stats["entries"] == 2  # A->B and B->A


# --- Errors -----------------------------------------------------------------


def test_unroutable_error_exists_and_is_a_runtime_error():
    """Guards the null-coercion failure mode described in routing/base.py."""
    assert issubclass(UnroutableError, RuntimeError)
