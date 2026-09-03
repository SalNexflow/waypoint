"""Travel-time providers and the factory that chooses between them."""

from __future__ import annotations

import logging
from pathlib import Path

from routing.base import (
    Coord,
    TravelMatrix,
    TravelTimeProvider,
    UnroutableError,
)
from routing.cache import CachingProvider, PairCache
from routing.frozen import FrozenProvider, cache_from_bundle, load_bundle
from routing.haversine import HaversineProvider, haversine_km
from routing.osrm import OSRMProvider

__all__ = [
    "CachingProvider",
    "Coord",
    "FrozenProvider",
    "HaversineProvider",
    "OSRMProvider",
    "PairCache",
    "TravelMatrix",
    "TravelTimeProvider",
    "UnroutableError",
    "build_provider",
    "haversine_km",
]

log = logging.getLogger("waypoint.routing")


async def build_provider(
    mode: str = "auto",
    *,
    osrm_url: str = "http://osrm:5000",
    cache_path: str | None = None,
    frozen_path: str | None = None,
    speed_kmh: float = 22.0,
    detour_factor: float = 1.35,
) -> TravelTimeProvider:
    """Return a cache-wrapped provider.

    mode:
      "osrm"       - require OSRM; raise if it is not serving
      "haversine"  - always the fallback, never touches the network
      "frozen"     - serve a precomputed matrix; raise on anything it lacks
      "auto"       - use OSRM if it answers, otherwise fall back loudly

    "auto" is the development default, but it degrades silently by nature,
    which is exactly the failure the spec warns about. Two things stop that
    being dangerous: the fallback logs a WARNING every time, and the returned
    matrices carry source="haversine", so `TravelMatrix.is_reportable` is
    False and the phase 10 benchmark refuses to publish figures from them.
    """
    if mode == "frozen":
        # The bundle is both the cache and the provider's provenance. Nothing
        # is wrapped around a network client here: there is no network to
        # reach, which is the entire point of this mode.
        if not frozen_path:
            raise ValueError(
                "routing_provider='frozen' requires FROZEN_MATRIX_PATH to be set"
            )
        bundle = load_bundle(Path(frozen_path))
        log.info(
            "routing: frozen matrix, %d pairs, source=%s, built %s from %s",
            bundle.size,
            bundle.source,
            bundle.built_at,
            bundle.graph,
        )
        if bundle.source != "osrm":
            log.warning(
                "routing: frozen matrix declares source=%s, NOT osrm. Every "
                "figure derived from it is provisional, not a result.",
                bundle.source,
            )
        # autosave=False: the bundle is a build artefact, not a growing cache.
        return CachingProvider(
            FrozenProvider(bundle), cache_from_bundle(bundle), autosave=False
        )

    cache = PairCache(Path(cache_path)) if cache_path else PairCache()

    if mode == "haversine":
        return CachingProvider(HaversineProvider(speed_kmh, detour_factor), cache)

    osrm = OSRMProvider(osrm_url)

    if mode == "osrm":
        if not await osrm.healthy():
            raise RuntimeError(
                f"routing_provider='osrm' but OSRM at {osrm_url} is not serving. "
                "Build the extract first: scripts/build_osrm.sh"
            )
        return CachingProvider(osrm, cache)

    if mode != "auto":
        raise ValueError(f"unknown routing mode {mode!r}")

    if await osrm.healthy():
        log.info("routing: using OSRM at %s", osrm_url)
        return CachingProvider(osrm, cache)

    log.warning(
        "routing: OSRM at %s is not serving; FALLING BACK TO HAVERSINE. "
        "Travel times will be optimistic by roughly a third and any figure "
        "derived from them is provisional, not a result.",
        osrm_url,
    )
    return CachingProvider(HaversineProvider(speed_kmh, detour_factor), cache)
