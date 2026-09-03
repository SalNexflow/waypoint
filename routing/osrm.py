"""OSRM client: real road-network travel times.

Uses the Table service, which computes a full N x N duration matrix in one
request -- far cheaper than N^2 route calls.

    GET /table/v1/driving/{lon},{lat};{lon},{lat};...?annotations=duration,distance

Note the coordinate order in that URL: OSRM takes **longitude first**, like
WKT and GeoJSON and unlike every way a human says a coordinate. The flip
happens in exactly one place below.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import httpx

from routing.base import Coord, TravelMatrix, UnroutableError

log = logging.getLogger("waypoint.routing.osrm")

# OSRM's own default cap on table requests is 100 coordinates. The compose
# service raises it (see --max-table-size); this is the client-side guard so
# the failure is a clear message rather than an HTTP 400 from a Lua error.
DEFAULT_MAX_TABLE_SIZE = 1000

# Anything beyond this suggests a job address that is nowhere near a road --
# a geocoding error, or a coordinate outside the extract. Warned, not fatal.
SNAP_WARN_METRES = 300.0


class OSRMProvider:
    """Travel matrices from a self-hosted OSRM instance."""

    name = "osrm"
    source = "osrm"

    def __init__(
        self,
        base_url: str,
        profile: str = "driving",
        timeout_s: float = 30.0,
        max_table_size: int = DEFAULT_MAX_TABLE_SIZE,
        health_timeout_s: float = 3.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.profile = profile
        self.timeout_s = timeout_s
        self.max_table_size = max_table_size
        # Separate, much shorter budget for the liveness probe. A real matrix
        # fetch can legitimately take seconds, but "is OSRM up" must answer
        # fast: build_provider() calls it on the auto-fallback path, so this
        # timeout is exactly how long a health endpoint hangs when the OSRM
        # container is stopped.
        self.health_timeout_s = health_timeout_s

    async def healthy(self) -> bool:
        """Round-trip a real table request to prove the server is serving.

        Deliberately not a bare TCP check: osrm-routed accepts connections
        before its data is usable, and a version endpoint would not catch a
        graph that failed to customise. This uses the same /table path the
        solver depends on, just with two coordinates and a short timeout.
        """
        a, b = Coord(3.1578, 101.7117), Coord(3.1466, 101.7113)
        url = (
            f"{self.base_url}/table/v1/{self.profile}/"
            f"{a.lon},{a.lat};{b.lon},{b.lat}"
        )
        try:
            async with httpx.AsyncClient(timeout=self.health_timeout_s) as client:
                resp = await client.get(url, params={"annotations": "duration"})
            if resp.status_code != 200:
                log.warning("OSRM health check: HTTP %s", resp.status_code)
                return False
            payload = resp.json()
            if payload.get("code") != "Ok":
                log.warning("OSRM health check: code %s", payload.get("code"))
                return False
            # A served-but-empty graph would return Ok with null durations.
            durations = payload.get("durations") or []
            return bool(durations) and durations[0][1] is not None
        except Exception as exc:  # noqa: BLE001 - health check reports, never raises
            log.warning("OSRM health check failed: %s", exc)
            return False

    async def matrix(self, coords: Sequence[Coord]) -> TravelMatrix:
        pts = tuple(coords)
        if not pts:
            raise ValueError("matrix() needs at least one coordinate")
        if len(pts) > self.max_table_size:
            raise ValueError(
                f"{len(pts)} coordinates exceeds max_table_size={self.max_table_size}. "
                "Raise --max-table-size on the osrm service, or chunk the request."
            )

        # The one place lat/lon is flipped to lon/lat for the wire.
        path = ";".join(f"{c.lon},{c.lat}" for c in pts)
        url = f"{self.base_url}/table/v1/{self.profile}/{path}"

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.get(
                url, params={"annotations": "duration,distance"}
            )

        if resp.status_code != 200:
            raise RuntimeError(
                f"OSRM returned HTTP {resp.status_code}: {resp.text[:300]}"
            )

        payload = resp.json()
        if payload.get("code") != "Ok":
            raise RuntimeError(
                f"OSRM error {payload.get('code')}: {payload.get('message', '')}"
            )

        raw_dur = payload.get("durations")
        raw_dist = payload.get("distances")
        if raw_dur is None:
            raise RuntimeError("OSRM response contained no durations")

        # A null means OSRM could not route that pair -- almost always a
        # coordinate outside the extract's bounding box. Coercing it to zero
        # would tell the solver two distant places are adjacent, so it is a
        # hard error naming the offending coordinates.
        unroutable: list[str] = []
        for i, row in enumerate(raw_dur):
            for j, v in enumerate(row):
                if v is None:
                    unroutable.append(f"{pts[i]} -> {pts[j]}")
        if unroutable:
            raise UnroutableError(
                f"OSRM could not route {len(unroutable)} pair(s); first few: "
                + "; ".join(unroutable[:3])
                + ". Usually means a coordinate lies outside the built extract."
            )

        durations = tuple(tuple(int(round(v)) for v in row) for row in raw_dur)
        if raw_dist is None:
            distances = tuple(tuple(0 for _ in row) for row in raw_dur)
        else:
            distances = tuple(
                tuple(int(round(v)) if v is not None else 0 for v in row)
                for row in raw_dist
            )

        # How far each input coordinate was moved to reach a road.
        snaps = [
            float(s.get("distance", 0.0) or 0.0)
            for s in payload.get("sources", [])
        ]
        max_snap = max(snaps, default=0.0)
        if max_snap > SNAP_WARN_METRES:
            worst = pts[snaps.index(max_snap)]
            log.warning(
                "OSRM snapped a coordinate %.0fm to reach a road: %s. "
                "Check the address or the extract bounds.",
                max_snap,
                worst,
            )

        matrix = TravelMatrix(
            coords=pts,
            durations=durations,
            distances=distances,
            source=self.source,
            max_snap_m=max_snap,
        )

        problems = matrix.sanity_problems()
        if problems:
            raise RuntimeError(
                "OSRM returned a structurally invalid matrix: " + "; ".join(problems[:5])
            )

        return matrix

    async def route_geometry(self, a: Coord, b: Coord) -> dict:
        """A single route with its geometry, for drawing lines on the map.

        Not used by the solver -- the solver only ever consumes the matrix.
        The phase 11 map needs the actual road polyline, and this is where it
        will come from.
        """
        path = f"{a.lon},{a.lat};{b.lon},{b.lat}"
        url = f"{self.base_url}/route/v1/{self.profile}/{path}"
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.get(
                url, params={"overview": "full", "geometries": "geojson"}
            )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != "Ok":
            raise RuntimeError(f"OSRM error {payload.get('code')}")
        return payload["routes"][0]
