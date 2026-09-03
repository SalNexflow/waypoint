"""Klang Valley geography for the seed generator.

Why not just scatter points uniformly over a bounding box: a uniform box over
KL puts jobs in the Titiwangsa forest reserve, in the Straits of Malacca, and
on the runway at Subang. It also makes the *benchmark dishonest*. Uniformly
scattered jobs have no cluster structure, so a greedy nearest-neighbour
baseline does unusually badly on them and the solver looks better than it is.

Real dispatch days are clustered -- a dozen jobs in PJ, a handful in Cheras,
two out in Klang. Generating that structure is the difference between a
benchmark that means something and a number that flatters the solver.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from routing.haversine import haversine_km as _haversine_km


@dataclass(frozen=True)
class District:
    """A named cluster centre with a plausible spread.

    `frozen=True` makes instances immutable and hashable. These are reference
    data -- nothing should ever mutate one -- and freezing turns a whole class
    of accidental-mutation bug into an AttributeError at the point of the
    mistake.
    """

    name: str
    lat: float
    lon: float
    spread_km: float
    # Relative likelihood of a *job* landing here. Commercial and high-density
    # areas draw far more service calls than outlying residential ones.
    job_weight: float
    # Relative likelihood of a *technician living* here. Roughly inverted:
    # technicians live in the affordable suburbs, not in KLCC.
    home_weight: float


# Centroids are approximate district centres, good to a few hundred metres.
# That is well inside the noise the spread introduces, and is plenty for
# generating plausible routing problems.
DISTRICTS: tuple[District, ...] = (
    District("KLCC",            3.1578, 101.7117, 1.2, 10.0,  1.0),
    District("Bukit Bintang",   3.1466, 101.7113, 1.2,  9.0,  1.0),
    District("Bangsar",         3.1290, 101.6790, 1.8,  7.0,  3.0),
    District("Mont Kiara",      3.1662, 101.6538, 1.6,  6.0,  2.0),
    District("Sentul",          3.1833, 101.6917, 2.2,  4.0,  5.0),
    District("Setapak",         3.2050, 101.7300, 2.5,  4.0,  6.0),
    District("Kepong",          3.2000, 101.6350, 2.8,  4.0,  6.0),
    District("Ampang",          3.1500, 101.7600, 2.5,  5.0,  4.0),
    District("Cheras",          3.0833, 101.7500, 3.2,  7.0,  8.0),
    District("Old Klang Road",  3.0900, 101.6700, 2.2,  5.0,  5.0),
    District("Petaling Jaya",   3.1073, 101.6067, 3.0,  9.0,  9.0),
    District("Subang Jaya",     3.0436, 101.5811, 2.8,  6.0,  7.0),
    District("Shah Alam",       3.0733, 101.5185, 3.5,  5.0,  6.0),
    District("Puchong",         3.0319, 101.6169, 3.0,  5.0,  7.0),
    District("Sri Petaling",    3.0567, 101.6900, 2.0,  4.0,  4.0),
    District("Seri Kembangan",  3.0250, 101.7050, 2.5,  3.0,  5.0),
    District("Kajang",          2.9930, 101.7880, 3.0,  2.5,  4.0),
    District("Klang",           3.0449, 101.4455, 3.5,  2.5,  3.0),
    District("Cyberjaya",       2.9188, 101.6520, 2.5,  2.0,  1.5),
    District("Putrajaya",       2.9264, 101.6964, 2.5,  2.0,  1.5),
)

# Parts depots. Real ones sit in industrial areas with cheap warehousing and
# ring-road access, not in the city centre.
DEPOT_SITES: tuple[tuple[str, float, float], ...] = (
    ("Shah Alam Depot", 3.0570, 101.5310),
    ("Cheras Depot",    3.0790, 101.7440),
    ("Sentul Depot",    3.1880, 101.6870),
)


# Near the equator a degree of longitude is almost exactly as long as a degree
# of latitude (cos(3 deg) = 0.9986), so the usual lat/lon distortion is
# negligible here. Applying the cosine anyway costs nothing and keeps the
# helper honest if anyone reuses it further north.
_KM_PER_DEG_LAT = 111.32


def km_to_deg(km: float, at_lat: float) -> tuple[float, float]:
    """Return (delta_lat_degrees, delta_lon_degrees) for a distance in km."""
    d_lat = km / _KM_PER_DEG_LAT
    d_lon = km / (_KM_PER_DEG_LAT * math.cos(math.radians(at_lat)))
    return d_lat, d_lon


# Re-exported from routing/, which owns the haversine maths for the whole
# project. Kept importable from here because the generator and its tests use
# it, but there is exactly one implementation.
#
# NOT the travel-time matrix. Straight-line distance in the Klang Valley
# understates real driving by roughly 30-40%. Nothing that produces a
# reportable number may call this -- see routing/base.py:is_reportable.
haversine_km = _haversine_km


# Average door-to-door speed in the Klang Valley including traffic, parking
# and the walk to the unit. Deliberately pessimistic: 22 km/h is about right
# for a working day in KL, and optimistic travel estimates produce seed days
# that are quietly impossible.
AVG_SPEED_KMH = 22.0


def estimate_travel_seconds(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> int:
    """Rough travel time, used only for feasibility estimates in the generator.

    Applies a 1.35x detour factor to straight-line distance -- the standard
    correction for road networks in a dense city.
    """
    km = haversine_km(lat1, lon1, lat2, lon2) * 1.35
    return int((km / AVG_SPEED_KMH) * 3600)


# Postcode per district, for generated addresses (field phase 3).
#
# A plain dict rather than a field on District: District is positional
# throughout the DISTRICTS tuple above, and adding a field there would mean
# rewriting twenty constructor calls for data only the seed's address builder
# ever reads. Keyed by name, which is already the district's identity.
DISTRICT_POSTCODES: dict[str, str] = {
    "KLCC": "50450",
    "Bukit Bintang": "55100",
    "Bangsar": "59100",
    "Mont Kiara": "50480",
    "Sentul": "51100",
    "Setapak": "53300",
    "Kepong": "52100",
    "Ampang": "68000",
    "Cheras": "56000",
    "Old Klang Road": "58100",
    "Petaling Jaya": "46000",
    "Subang Jaya": "47500",
    "Shah Alam": "40000",
    "Puchong": "47100",
    "Sri Petaling": "57000",
    "Seri Kembangan": "43300",
    "Kajang": "43000",
    "Klang": "41000",
    "Cyberjaya": "63000",
    "Putrajaya": "62000",
}
