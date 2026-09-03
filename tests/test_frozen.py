"""The frozen matrix: what it serves, and what it refuses to serve.

The point of these tests is the refusal. A precomputed matrix that silently
degraded would be worse than no precomputed matrix at all -- it would keep
answering, and every answer after the first uncovered address would be wrong
in a way nothing downstream could detect.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from routing import build_provider
from routing.base import Coord, UnroutableError
from routing.cache import PairCache
from routing.frozen import (
    FORMAT_VERSION,
    FrozenProvider,
    cache_from_bundle,
    load_bundle,
    write_bundle,
)

A = Coord(3.1578, 101.7117)
B = Coord(3.1466, 101.7113)
C = Coord(3.0733, 101.5185)  # never written into any bundle below


def run(coro):
    return asyncio.run(coro)


def _cache(*pairs: tuple[Coord, Coord, int, int]) -> PairCache:
    cache = PairCache(path=None)
    for a, b, dur, dist in pairs:
        cache.put(a, b, dur, dist)
    return cache


def _bundle_file(tmp_path, source="osrm"):
    path = tmp_path / "frozen.json"
    write_bundle(
        path,
        _cache((A, B, 600, 4200), (B, A, 660, 4300)),
        source=source,
        graph="test-graph",
    )
    return path


# --- Round trip -----------------------------------------------------------


def test_bundle_round_trips(tmp_path):
    path = _bundle_file(tmp_path)
    bundle = load_bundle(path)

    assert bundle.source == "osrm"
    assert bundle.graph == "test-graph"
    assert bundle.size == 2

    cache = cache_from_bundle(bundle)
    assert cache.get(A, B) == (600, 4200)
    # Direction matters. A->B and B->A are separate entries and must not be
    # collapsed: one-way streets are the reason the matrix is asymmetric.
    assert cache.get(B, A) == (660, 4300)


def test_bundle_cache_never_writes_back(tmp_path):
    """A bundle is a build artefact, not a cache that grows at runtime."""
    bundle = load_bundle(_bundle_file(tmp_path))
    cache = cache_from_bundle(bundle)
    assert cache.path is None
    cache.save()  # must be a no-op rather than an error
    assert load_bundle(_bundle_file(tmp_path)).size == 2


# --- Provenance -----------------------------------------------------------


def test_source_comes_from_the_file_not_the_class(tmp_path):
    """The provider reports what it was given, so it cannot launder a number.

    Freeze haversine and every matrix built from it stays unreportable,
    exactly as live haversine would be. This is the property that makes a
    precomputed deployment honest rather than convenient.
    """
    osrm_bundle = load_bundle(_bundle_file(tmp_path / "a"))
    hav_bundle = load_bundle(_bundle_file(tmp_path / "b", source="haversine"))

    assert FrozenProvider(osrm_bundle).source == "osrm"
    assert FrozenProvider(hav_bundle).source == "haversine"


def test_matrix_from_frozen_osrm_is_reportable(tmp_path):
    provider = run(build_provider("frozen", frozen_path=str(_bundle_file(tmp_path))))
    matrix = run(provider.matrix([A, B]))

    assert matrix.source == "osrm"
    assert matrix.is_reportable is True
    assert matrix.duration(0, 1) == 600
    assert matrix.duration(1, 0) == 660
    assert matrix.duration(0, 0) == 0
    assert matrix.sanity_problems() == []


def test_matrix_from_frozen_haversine_is_not_reportable(tmp_path):
    path = _bundle_file(tmp_path, source="haversine")
    provider = run(build_provider("frozen", frozen_path=str(path)))
    matrix = run(provider.matrix([A, B]))

    assert matrix.source == "haversine"
    assert matrix.is_reportable is False


# --- The refusal ----------------------------------------------------------


def test_uncovered_location_raises_rather_than_degrading(tmp_path):
    provider = run(build_provider("frozen", frozen_path=str(_bundle_file(tmp_path))))

    with pytest.raises(UnroutableError) as exc:
        run(provider.matrix([A, B, C]))

    message = str(exc.value)
    # The dispatcher needs to know WHICH address, not merely that something
    # failed -- that is the whole difference between a loud failure and a
    # useless one.
    assert "3.0733" in message and "101.5185" in message
    assert "1 location" in message


def test_partial_coverage_is_reported_differently_from_a_new_address(tmp_path):
    """A half-warmed bundle is a different fault and says so.

    Every location here appears in some pair, so calling any of them "not in
    the matrix" would be wrong. The message has to distinguish the two.
    """
    path = tmp_path / "partial.json"
    write_bundle(
        path,
        _cache((A, B, 600, 4200), (B, A, 660, 4300), (A, C, 900, 7000)),
        source="osrm",
        graph="test-graph",
    )
    provider = run(build_provider("frozen", frozen_path=str(path)))

    with pytest.raises(UnroutableError) as exc:
        run(provider.matrix([A, B, C]))

    message = str(exc.value)
    assert "not in the frozen travel matrix at all" not in message
    assert "different set of locations" in message


# --- Loading failures are loud too ---------------------------------------


def test_missing_file_names_the_fix(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        load_bundle(tmp_path / "nope.json")
    assert "freeze_matrix" in str(exc.value)


def test_bundle_without_provenance_is_refused(tmp_path):
    path = tmp_path / "no-source.json"
    path.write_text(
        json.dumps({"version": FORMAT_VERSION, "pairs": {"a|b": [1, 2]}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="declares no source"):
        load_bundle(path)


def test_future_format_version_is_refused(tmp_path):
    path = tmp_path / "future.json"
    path.write_text(
        json.dumps(
            {
                "version": FORMAT_VERSION + 1,
                "source": "osrm",
                "pairs": {"a|b": [1, 2]},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="format version"):
        load_bundle(path)


def test_empty_bundle_is_refused(tmp_path):
    """Unlike PairCache, which starts empty on a bad file and moves on.

    Right for a cache -- the cost is one OSRM round-trip. Wrong here: an empty
    frozen matrix cannot answer anything, and failing at startup beats failing
    at the first solve.
    """
    path = tmp_path / "empty.json"
    path.write_text(
        json.dumps({"version": FORMAT_VERSION, "source": "osrm", "pairs": {}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no pairs"):
        load_bundle(path)


def test_frozen_mode_requires_a_path():
    with pytest.raises(ValueError, match="FROZEN_MATRIX_PATH"):
        run(build_provider("frozen", frozen_path=None))
