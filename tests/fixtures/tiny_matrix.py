"""Frozen travel matrix for the tiny instance.

Captured from live OSRM on 2026-09-02, then pinned. The four hand-written
schedules in tiny_schedules.py were computed against exactly these numbers;
if the matrix drifted when OSM data updated upstream, a schedule that is
valid by four minutes could silently become invalid, and the phase 5 exercise
would be testing the wrong thing.

Node order matches solver/tiny.py:

    0 = T1 (Ahmad, KLCC)         3 = J2 (Plaza Bukit Bintang)
    1 = T2 (Siti, Petaling Jaya) 4 = J3 (Wisma PJ Chiller)
    2 = J1 (Menara KLCC)         5 = J4 (Bangsar Medical)
                                 6 = J5 (Mont Kiara Residency)

Durations are seconds, distances metres. The matrix is asymmetric, as any
real road network is.
"""

from __future__ import annotations

from routing.base import Coord, TravelMatrix
from solver import tiny

DURATIONS = (
    (0, 1447, 410, 407, 1384, 764, 959),
    (1564, 0, 1415, 1305, 199, 903, 1027),
    (356, 1346, 0, 267, 1290, 669, 864),
    (446, 1258, 300, 0, 1185, 593, 894),
    (1491, 162, 1343, 1233, 0, 831, 1036),
    (881, 1009, 684, 574, 936, 0, 688),
    (1116, 970, 911, 965, 983, 818, 0),
)

DISTANCES = (
    (0, 17834, 4010, 3630, 18194, 8108, 11347),
    (20842, 0, 19309, 17963, 1608, 11406, 12416),
    (1836, 17192, 0, 2682, 17743, 7656, 10896),
    (3422, 17660, 3436, 0, 15959, 6313, 11072),
    (20933, 1387, 19399, 18054, 0, 11496, 12740),
    (8052, 13016, 7487, 6141, 11315, 0, 8717),
    (13586, 12516, 12717, 11972, 12818, 8683, 0),
)


def matrix() -> TravelMatrix:
    return TravelMatrix(
        coords=tuple(Coord(c.lat, c.lon) for c in tiny.coords()),
        durations=DURATIONS,
        distances=DISTANCES,
        source="osrm",
    )


def problem():
    """The tiny Problem on the frozen matrix. Hermetic: no OSRM needed."""
    return tiny.build(matrix())
