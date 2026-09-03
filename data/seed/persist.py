"""Write a generated instance into Postgres.

The only module in the seed package that knows the database exists. Keeping
the generator pure means tests and the phase 10 benchmark can build instances
without a running stack.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from geoalchemy2.elements import WKTElement
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.tables import Assignment, Depot, Job, SolveRun, Technician
from data.seed.generate import SeedInstance


def point(lat: float, lon: float) -> WKTElement:
    """Build a PostGIS point from a latitude and longitude.

    WKT is X-then-Y, and for geographic coordinates X is LONGITUDE. So it is
    POINT(lon lat) -- the opposite order to how coordinates are spoken, written
    on maps, and returned by every geocoding API.

    Getting this backwards does not raise. It silently places every job in the
    wrong hemisphere: POINT(3.15 101.71) is valid, and is somewhere in Kenya.
    Every distance would be wrong, every route absurd, and the solver would
    still return a confident-looking schedule.

    This function exists so the order is written down exactly once.
    """
    return WKTElement(f"POINT({lon} {lat})", srid=4326)


async def truncate_all(session: AsyncSession) -> None:
    """Clear all domain data and reset id sequences.

    RESTART IDENTITY matters for reproducibility: without it, re-seeding the
    same instance twice yields different primary keys, and any test or
    benchmark that refers to "job 12" drifts.
    """
    await session.execute(
        text(
            "TRUNCATE TABLE assignments, solve_runs, jobs, technicians, depots "
            "RESTART IDENTITY CASCADE"
        )
    )


async def persist_instance(
    session: AsyncSession,
    inst: SeedInstance,
    *,
    truncate: bool = False,
) -> dict[str, dict[str, int]]:
    """Insert an instance and return {kind: {ref: database_id}}.

    The caller commits. This function does not, so a seed can be composed
    into a larger transaction (the benchmark harness will want exactly that).
    """
    if truncate:
        await truncate_all(session)

    tz = ZoneInfo(inst.timezone)

    depot_ids: dict[str, int] = {}
    for d in inst.depots:
        row = Depot(
            name=d.name,
            location=point(d.lat, d.lon),
            stocked_parts=d.stocked_parts,
        )
        session.add(row)
        await session.flush()  # assigns row.id without committing
        depot_ids[d.ref] = row.id

    tech_ids: dict[str, int] = {}
    for t in inst.technicians:
        row = Technician(
            name=t.name,
            skills=list(t.skills),
            shift_start=t.shift_start,
            shift_end=t.shift_end,
            home_location=point(t.home_lat, t.home_lon),
            van_stock=t.van_stock,
            max_jobs=t.max_jobs,
        )
        session.add(row)
        await session.flush()
        tech_ids[t.ref] = row.id

    job_ids: dict[str, int] = {}
    for j in inst.jobs:
        row = Job(
            customer=j.customer,
            location=point(j.lat, j.lon),
            duration_seconds=j.duration_seconds,
            required_skills=list(j.required_skills),
            required_parts=list(j.required_parts),
            hard_window_start=j.hard_window_start.astimezone(tz),
            hard_window_end=j.hard_window_end.astimezone(tz),
            pref_window_start=(
                j.pref_window_start.astimezone(tz) if j.pref_window_start else None
            ),
            pref_window_end=(
                j.pref_window_end.astimezone(tz) if j.pref_window_end else None
            ),
            priority=j.priority,
            status="pending",
            # Job detail for the technician PWA. Empty string -> NULL: the
            # generator uses "" for absent, the column uses NULL, and mixing
            # the two would make `notes IS NULL` disagree with `notes = ''`.
            area=j.district or None,
            address=j.address or None,
            phone=j.phone or None,
            service_type=j.service_type or None,
            fault_description=j.fault_description or None,
            notes=j.note or None,
        )
        session.add(row)
        await session.flush()
        job_ids[j.ref] = row.id

    return {"depots": depot_ids, "technicians": tech_ids, "jobs": job_ids}


async def counts(session: AsyncSession) -> dict[str, int]:
    """Row counts per domain table, for verifying a seed landed."""
    out: dict[str, int] = {}
    for name, model in (
        ("technicians", Technician),
        ("depots", Depot),
        ("jobs", Job),
        ("solve_runs", SolveRun),
        ("assignments", Assignment),
    ):
        result = await session.execute(
            text(f"SELECT count(*) FROM {model.__tablename__}")  # noqa: S608
        )
        out[name] = result.scalar_one()
    return out


async def verify_roundtrip(session: AsyncSession, inst: SeedInstance) -> list[str]:
    """Read back what was written and check it survived the trip.

    Specifically checks that coordinates came back where they went in. If
    point() ever gets its lat/lon order wrong, this catches it immediately
    rather than at phase 3 when the travel matrix looks strange.
    """
    problems: list[str] = []

    rows = (
        await session.execute(
            text(
                "SELECT customer, ST_Y(location::geometry) AS lat, "
                "ST_X(location::geometry) AS lon FROM jobs ORDER BY id LIMIT 5"
            )
        )
    ).all()

    for row, expected in zip(rows, inst.jobs, strict=False):
        if abs(row.lat - expected.lat) > 1e-6 or abs(row.lon - expected.lon) > 1e-6:
            problems.append(
                f"{expected.ref}: wrote ({expected.lat}, {expected.lon}), "
                f"read back ({row.lat}, {row.lon})"
            )
        # Klang Valley sanity envelope. Catches a lat/lon swap even if the
        # write and read are consistently wrong in the same direction.
        if not (2.5 <= row.lat <= 3.6) or not (101.2 <= row.lon <= 102.1):
            problems.append(
                f"{expected.ref}: ({row.lat}, {row.lon}) is outside the Klang Valley"
            )

    return problems
