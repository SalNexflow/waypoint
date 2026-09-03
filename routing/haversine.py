"""Great-circle fallback provider.

Exists so solver work is never blocked on OSRM. It is deliberately not good
enough to benchmark with: straight-line distance in the Klang Valley
understates real driving by roughly 30-40% because of rivers, limited-access
highways and one-way systems, and no fudge factor recovers the structure of a
road network. Matrices from here carry source="haversine" and
is_reportable=False.

This module is the canonical home of the haversine maths for the whole
project; data/seed/geography.py imports from here.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from routing.base import Coord, TravelMatrix

EARTH_RADIUS_KM = 6371.0088

# Average door-to-door speed in the Klang Valley including traffic and
# parking. Deliberately pessimistic; optimistic travel estimates produce
# schedules that are quietly impossible.
DEFAULT_SPEED_KMH = 22.0

# Road distance divided by straight-line distance. 1.35 is the standard
# correction for a dense city grid. OSRM will report the real figure per
# route, and comparing the two is a good sanity check on both.
DEFAULT_DETOUR_FACTOR = 1.35


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


class HaversineProvider:
    """Travel times from straight-line distance and an assumed speed.

    Satisfies TravelTimeProvider structurally -- note it inherits from
    nothing. See the Protocol docstring in routing/base.py.
    """

    name = "haversine"
    source = "haversine"

    def __init__(
        self,
        speed_kmh: float = DEFAULT_SPEED_KMH,
        detour_factor: float = DEFAULT_DETOUR_FACTOR,
    ) -> None:
        if speed_kmh <= 0:
            raise ValueError("speed_kmh must be positive")
        if detour_factor < 1.0:
            raise ValueError("detour_factor below 1.0 implies driving through walls")
        self.speed_kmh = speed_kmh
        self.detour_factor = detour_factor

    async def healthy(self) -> bool:
        """Always available -- that is the entire point of this provider."""
        return True

    async def matrix(self, coords: Sequence[Coord]) -> TravelMatrix:
        pts = tuple(coords)
        n = len(pts)

        durations: list[tuple[int, ...]] = []
        distances: list[tuple[int, ...]] = []

        for a in pts:
            d_row: list[int] = []
            m_row: list[int] = []
            for b in pts:
                if a == b:
                    d_row.append(0)
                    m_row.append(0)
                    continue
                km = haversine_km(a.lat, a.lon, b.lat, b.lon) * self.detour_factor
                m_row.append(int(km * 1000))
                d_row.append(int((km / self.speed_kmh) * 3600))
            durations.append(tuple(d_row))
            distances.append(tuple(m_row))

        return TravelMatrix(
            coords=pts,
            durations=tuple(durations),
            distances=tuple(distances),
            source=self.source,
        )
