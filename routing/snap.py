"""Move generated coordinates onto the road network.

Seeded points are drawn from a Gaussian around a district centre, so a few
land in a park, a lake, or the middle of a golf course. OSRM copes -- it snaps
each one to the nearest routable road before computing anything -- but it does
so silently on every single request, and a point that has to travel 400m to
reach a road is not the point anyone thinks they are routing to. The logged
distances between stops are then measured from somewhere the job is not.

Snapping once, at generation time, fixes the data rather than papering over it:

  * the stored coordinate is the one actually routed from, so the map pin and
    the travel time finally agree;
  * `max_snap_m` on every subsequent matrix drops to roughly zero, which turns
    the warning back into a real signal -- if it fires after this, something
    genuinely is outside the extract bounds;
  * it is idempotent. Snapping an already-snapped point returns it unchanged,
    so re-running is safe.

Determinism: OSRM's /nearest is a pure function of the graph, so the same seed
against the same graph gives the same snapped day on any machine. It does mean
seeded coordinates now depend on the OSRM build, which is why `snap_coords`
reports what it did rather than doing it invisibly.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from routing.base import Coord

log = logging.getLogger("waypoint.routing.snap")

# Collects transport errors during one snap_coords() call so they can be
# reported as a single line rather than one per coordinate.
_unreachable: list[str] = []

# Below this, snapping is noise -- a point already on a road, moved a couple of
# metres to the road centreline. Leaving those alone keeps the generated data
# recognisably the generator's rather than OSRM's.
MIN_MOVE_M = 15.0

# Above this, do NOT snap. A point this far from any road is not a mis-drawn
# address, it is a bug -- outside the extract bounds, or lat/lon swapped.
# Silently dragging it a kilometre would hide exactly the error the warning
# exists to surface.
MAX_MOVE_M = 2000.0


class SnapResult:
    """What snapping did, so a caller can report it rather than guess."""

    def __init__(self) -> None:
        self.moved = 0
        self.unchanged = 0
        self.refused: list[tuple[Coord, float]] = []
        self.max_move_m = 0.0
        self.total_move_m = 0.0
        self.unreachable = 0

    @property
    def mean_move_m(self) -> float:
        return self.total_move_m / self.moved if self.moved else 0.0

    def __str__(self) -> str:
        parts = [
            f"snapped {self.moved}",
            f"already on a road {self.unchanged}",
            f"max {self.max_move_m:.0f}m",
        ]
        if self.moved:
            parts.append(f"mean {self.mean_move_m:.0f}m")
        if self.refused:
            parts.append(f"REFUSED {len(self.refused)} over {MAX_MOVE_M:.0f}m")
        if self.unreachable:
            parts.append(f"OSRM UNREACHABLE for {self.unreachable}")
        return ", ".join(parts)


async def _nearest(
    client: httpx.AsyncClient, base_url: str, profile: str, c: Coord
) -> tuple[Coord, float] | None:
    """One /nearest lookup. Returns (snapped coord, metres moved)."""
    # OSRM takes lon,lat -- the opposite order to Coord. This is the only
    # place in this module that ordering appears.
    url = f"{base_url}/nearest/v1/{profile}/{c.lon:.6f},{c.lat:.6f}"
    try:
        resp = await client.get(url, params={"number": 1})
    except httpx.HTTPError as exc:
        # Deliberately not logged per coordinate. With OSRM down that is one
        # scary line per point -- 51 of them on the default seed, which buries
        # the single actionable message under noise on someone's first run.
        # Counted instead, and summarised once by the caller.
        _unreachable.append(str(exc))
        return None
    if resp.status_code != 200:
        return None
    payload = resp.json()
    waypoints = payload.get("waypoints") or []
    if not waypoints:
        return None
    lon, lat = waypoints[0]["location"]
    return Coord(lat=round(lat, 6), lon=round(lon, 6)), float(
        waypoints[0].get("distance", 0.0) or 0.0
    )


async def snap_coords(
    coords: list[Coord],
    base_url: str,
    *,
    profile: str = "driving",
    concurrency: int = 16,
    timeout_s: float = 30.0,
) -> tuple[list[Coord], SnapResult]:
    """Return coordinates moved onto the road network, plus what changed.

    Never raises on an unreachable OSRM: the original coordinate is kept and
    the caller decides whether that is acceptable. Seeding must not become
    impossible just because the routing container is down.
    """
    result = SnapResult()
    _unreachable.clear()
    out = list(coords)
    sem = asyncio.Semaphore(concurrency)
    base_url = base_url.rstrip("/")

    async with httpx.AsyncClient(timeout=timeout_s) as client:

        async def one(i: int, c: Coord) -> None:
            async with sem:
                got = await _nearest(client, base_url, profile, c)
            if got is None:
                result.unchanged += 1
                return
            snapped, metres = got
            if metres > MAX_MOVE_M:
                result.refused.append((c, metres))
                result.unchanged += 1
                return
            result.max_move_m = max(result.max_move_m, metres)
            if metres < MIN_MOVE_M:
                result.unchanged += 1
                return
            out[i] = snapped
            result.moved += 1
            result.total_move_m += metres

        await asyncio.gather(*(one(i, c) for i, c in enumerate(coords)))

    if _unreachable:
        log.warning(
            "OSRM at %s was unreachable for %d of %d coordinates (%s). "
            "Coordinates were left exactly as generated -- start it with "
            "`docker compose --profile osrm up -d osrm` and re-seed to snap "
            "them onto the road network.",
            base_url,
            len(_unreachable),
            len(coords),
            _unreachable[0],
        )
        result.unreachable = len(_unreachable)
        _unreachable.clear()

    for coord, metres in result.refused:
        log.warning(
            "Refusing to snap %s: nearest road is %.0fm away, which is too far "
            "to be an imprecise address. Check the extract bounds.",
            coord,
            metres,
        )
    return out, result
