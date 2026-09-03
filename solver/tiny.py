"""The phase 4 instance: 5 jobs, 2 technicians.

Hand-written rather than generated, on purpose. Every duration is a round
number of minutes and every window falls on the hour, so a schedule can be
checked with mental arithmetic. That matters more than realism here: the
phase 5 checker has to be tested against schedules whose correctness you can
establish independently of any code.

Travel times are real, from OSRM, so the instance still exercises the phase 3
matrix. Only the jobs and shifts are synthetic.

The structure is chosen to make skill and part matching *bind*:

    J3 needs chiller + refrigerant_handling and gas_r410a  -> only Siti
    J4 needs electrical and contactor                      -> only Ahmad
    J1, J2, J5 need split_unit and filter_set              -> either

So a valid schedule cannot be produced by putting everything on one person,
and the two forced assignments anchor opposite ends of the city.
"""

from __future__ import annotations

from datetime import date, time

from routing.base import TravelMatrix
from solver.problem import (
    Problem,
    ProblemJob,
    ProblemTech,
    build_problem,
    coords_for,
    seconds_since_midnight,
)

DAY = date(2026, 9, 3)
TIMEZONE = "Asia/Kuala_Lumpur"


def _t(h: int, m: int = 0) -> int:
    return seconds_since_midnight(time(h, m))


TECHNICIANS: list[ProblemTech] = [
    ProblemTech(
        ref="T1",
        name="Ahmad Faizal",
        node=0,
        skills=frozenset({"split_unit", "electrical"}),
        van_stock={"filter_set": 6, "contactor": 3},
        shift_start_s=_t(8),
        shift_end_s=_t(17),
        max_jobs=5,
        lat=3.1578,   # KLCC
        lon=101.7117,
    ),
    ProblemTech(
        ref="T2",
        name="Siti Nurhaliza",
        node=1,
        skills=frozenset({"split_unit", "chiller", "refrigerant_handling"}),
        van_stock={"filter_set": 6, "gas_r410a": 2},
        shift_start_s=_t(8),
        shift_end_s=_t(17),
        max_jobs=5,
        lat=3.1073,   # Petaling Jaya
        lon=101.6067,
    ),
]

JOBS: list[ProblemJob] = [
    ProblemJob(
        ref="J1",
        name="Menara KLCC",
        node=2,
        duration_s=60 * 60,
        skills=frozenset({"split_unit"}),
        parts=frozenset({"filter_set"}),
        hard_start_s=_t(8),
        hard_end_s=_t(12),
        pref_start_s=_t(8),
        pref_end_s=_t(10),
        priority=1,
        lat=3.1590,
        lon=101.7150,
    ),
    ProblemJob(
        ref="J2",
        name="Plaza Bukit Bintang",
        node=3,
        duration_s=60 * 60,
        skills=frozenset({"split_unit"}),
        parts=frozenset({"filter_set"}),
        hard_start_s=_t(9),
        hard_end_s=_t(13),
        pref_start_s=None,
        pref_end_s=None,
        priority=1,
        lat=3.1466,
        lon=101.7113,
    ),
    ProblemJob(
        ref="J3",
        name="Wisma PJ Chiller",
        node=4,
        duration_s=90 * 60,
        skills=frozenset({"chiller", "refrigerant_handling"}),
        parts=frozenset({"gas_r410a"}),
        hard_start_s=_t(9),
        hard_end_s=_t(15),
        pref_start_s=_t(9),
        pref_end_s=_t(12),
        priority=2,
        # Deliberately offset from T2's home. Placing a job on a technician's
        # doorstep gives a zero-travel pair, which is unrepresentative and can
        # hide a model that ignores travel entirely.
        lat=3.0995,
        lon=101.6115,
    ),
    ProblemJob(
        ref="J4",
        name="Bangsar Medical",
        node=5,
        duration_s=60 * 60,
        skills=frozenset({"electrical"}),
        parts=frozenset({"contactor"}),
        hard_start_s=_t(10),
        hard_end_s=_t(16),
        pref_start_s=None,
        pref_end_s=None,
        priority=1,
        lat=3.1290,
        lon=101.6790,
    ),
    ProblemJob(
        ref="J5",
        name="Mont Kiara Residency",
        node=6,
        duration_s=60 * 60,
        skills=frozenset({"split_unit"}),
        parts=frozenset({"filter_set"}),
        hard_start_s=_t(12),
        hard_end_s=_t(17),
        pref_start_s=None,
        pref_end_s=None,
        priority=1,
        lat=3.1662,
        lon=101.6538,
    ),
]


def coords():
    return coords_for(TECHNICIANS, JOBS)


def build(travel: TravelMatrix) -> Problem:
    """Build the tiny Problem around a travel matrix you supply.

    Takes the matrix as an argument rather than fetching one, so tests can
    inject a fixed matrix and stay hermetic while the CLI uses real OSRM.
    """
    return build_problem(
        day=DAY,
        timezone=TIMEZONE,
        technicians=list(TECHNICIANS),
        jobs=list(JOBS),
        travel=travel,
    )


async def build_live(mode: str = "osrm", osrm_url: str = "http://osrm:5000") -> Problem:
    """Build the tiny Problem against a live travel provider."""
    from routing import build_provider

    provider = await build_provider(mode, osrm_url=osrm_url)
    matrix = await provider.matrix(coords())
    return build(matrix)


if __name__ == "__main__":
    import asyncio

    from solver.problem import describe

    async def main() -> None:
        print(describe(await build_live()))

    asyncio.run(main())
