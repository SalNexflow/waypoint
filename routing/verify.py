"""Verify OSRM against routes a Klang Valley local can sanity-check.

    docker compose exec api python -m routing.verify

The spec is blunt about why this exists: "if the matrix is wrong, every
schedule is wrong and nothing about the output will look off." A travel matrix
has no tell. A schedule built on bad durations looks perfectly reasonable on a
map. So the numbers get checked against reality before anything is built on
them.

READ THIS BEFORE JUDGING THE NUMBERS
------------------------------------
OSRM's default car profile is **free-flow**: speed limits and road classes,
with no traffic model whatsoever. Your lived experience of these routes
includes traffic. Expect OSRM to be optimistic by roughly 30-50% against a
weekday rush hour, and to look about right against a quiet Sunday morning.

That gap is a known limitation, not a bug, and the phase 10 benchmark states
it rather than pretending otherwise. What you are checking here is whether the
numbers are *structurally* sane -- that the router is following real roads at
plausible speeds -- not whether they match your commute this morning.
"""

from __future__ import annotations

import asyncio
import sys

from routing.base import Coord
from routing.haversine import HaversineProvider, haversine_km
from routing.osrm import OSRMProvider

# Well-known Klang Valley landmarks. Coordinates are the recognisable public
# point of each -- the tower base, the station concourse, the mall entrance.
LANDMARKS: dict[str, Coord] = {
    "KLCC":          Coord(3.1578, 101.7117),
    "KL Sentral":    Coord(3.1339, 101.6869),
    "Mid Valley":    Coord(3.1177, 101.6773),
    "1 Utama":       Coord(3.1502, 101.6156),
    "Sunway Pyramid": Coord(3.0726, 101.6068),
    "Batu Caves":    Coord(3.2379, 101.6840),
    "Shah Alam":     Coord(3.0733, 101.5185),
    "Klang":         Coord(3.0449, 101.4455),
    "KLIA":          Coord(2.7456, 101.7099),
    "Cheras":        Coord(3.1044, 101.7395),
}

# Chosen to span route types: short urban, city-to-suburb, cross-town,
# suburb-to-suburb, and one long highway run. A router can look fine on one
# type and be badly wrong on another.
ROUTES: tuple[tuple[str, str], ...] = (
    ("KLCC", "KL Sentral"),        # short urban hop
    ("KLCC", "1 Utama"),           # city to suburb, the classic run
    ("Mid Valley", "Sunway Pyramid"),  # cross-town via Federal Highway
    ("KLCC", "Batu Caves"),        # north, mixed roads
    ("Shah Alam", "Klang"),        # suburb to suburb
    ("KL Sentral", "KLIA"),        # long highway run
)


def _mins(seconds: int) -> str:
    return f"{seconds / 60:.0f}m"


async def main() -> int:
    import os

    osrm_url = os.environ.get("OSRM_URL", "http://osrm:5000")
    osrm = OSRMProvider(osrm_url)
    hav = HaversineProvider()

    print(__doc__.split("READ THIS")[0].strip())
    print()

    if not await osrm.healthy():
        print(f"OSRM at {osrm_url} is not serving.")
        print()
        print("Build the graph and start it:")
        print("  ./scripts/build_osrm.sh")
        print("  docker compose --profile osrm up -d osrm")
        return 1

    # One matrix over every landmark used, so this is a single OSRM call and
    # exercises the same Table code path the solver will use.
    names = sorted({n for pair in ROUTES for n in pair})
    coords = [LANDMARKS[n] for n in names]
    idx = {n: i for i, n in enumerate(names)}

    m_osrm = await osrm.matrix(coords)
    m_hav = await hav.matrix(coords)

    problems = m_osrm.sanity_problems()
    if problems:
        print("MATRIX FAILED STRUCTURAL CHECKS:")
        for p in problems:
            print(f"  {p}")
        return 1

    print(f"{'route':<28} {'direct':>7} {'road':>7} {'detour':>7} "
          f"{'OSRM':>6} {'km/h':>6} {'haversine':>10} {'delta':>7}")
    print("-" * 88)

    understated: list[float] = []

    for a, b in ROUTES:
        i, j = idx[a], idx[b]
        direct_km = haversine_km(
            LANDMARKS[a].lat, LANDMARKS[a].lon,
            LANDMARKS[b].lat, LANDMARKS[b].lon,
        )
        road_km = m_osrm.distance(i, j) / 1000
        secs = m_osrm.duration(i, j)
        hav_secs = m_hav.duration(i, j)

        detour = road_km / direct_km if direct_km else 0
        kmh = (road_km / (secs / 3600)) if secs else 0
        delta_pct = ((hav_secs - secs) / secs * 100) if secs else 0
        understated.append(delta_pct)

        print(
            f"{a + ' -> ' + b:<28} {direct_km:>6.1f}k {road_km:>6.1f}k "
            f"{detour:>7.2f} {_mins(secs):>6} {kmh:>6.0f} "
            f"{_mins(hav_secs):>10} {delta_pct:>+6.0f}%"
        )

    print()
    print("Columns")
    print("  direct     straight-line distance (haversine)")
    print("  road       distance OSRM actually drove")
    print("  detour     road / direct. Healthy urban range is 1.2-1.6.")
    print("             Near 1.00 would mean OSRM is not following roads.")
    print("  OSRM       free-flow duration, no traffic")
    print("  km/h       implied average speed. Expect 25-40 in town,")
    print("             70-100 on the KLIA highway run. Uniform speed across")
    print("             every route would mean road classes are being ignored.")
    print("  haversine  what the fallback provider would have said")
    print("  delta      how much the fallback differs from OSRM")
    print()

    mean_delta = sum(understated) / len(understated)
    print(f"Haversine differs from OSRM by {mean_delta:+.0f}% on average across "
          f"these routes.")
    print("That gap is why haversine matrices are marked is_reportable=False.")
    print()
    print(f"Coordinates snapped at most {m_osrm.max_snap_m:.0f}m to reach a road.")
    print()
    print("REMEMBER: OSRM here is free-flow with no traffic model. Against a")
    print("weekday rush hour these will look optimistic by 30-50%. Against a")
    print("quiet Sunday they should look about right. You are checking that")
    print("the router follows real roads at plausible speeds.")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
