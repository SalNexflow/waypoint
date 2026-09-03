"""CLI for the seed generator.

    docker compose exec api python -m data.seed --jobs 40 --technicians 8 \
        --day 2026-09-03 --seed 42 --truncate

Add --dry-run to generate and inspect a day without touching the database.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import date, datetime

from data.seed.generate import (
    snap_instance,
    SeedInstance,
    generate_instance,
    load_ratio,
    validate_instance,
)
from data.seed.geography import estimate_travel_seconds


def _hhmm(total_seconds: float) -> str:
    m = int(total_seconds // 60)
    return f"{m // 60}h{m % 60:02d}m"


def summarise(inst: SeedInstance) -> str:
    lines: list[str] = []
    add = lines.append

    add(f"Instance  day={inst.day}  seed={inst.seed}  tz={inst.timezone}")
    add(f"          {len(inst.jobs)} jobs, {len(inst.technicians)} technicians, "
        f"{len(inst.depots)} depots")
    add("")

    # --- Technicians ---
    add("Technicians")
    add(f"  {'ref':<4} {'name':<18} {'shift':<14} {'max':>3}  skills")
    for t in inst.technicians:
        shift = f"{t.shift_start:%H:%M}-{t.shift_end:%H:%M}"
        add(f"  {t.ref:<4} {t.name:<18} {shift:<14} {t.max_jobs:>3}  "
            f"{', '.join(t.skills)}")
    add("")

    # --- Skill supply vs demand. The most useful table here: a skill in high
    # demand and short supply is where unassigned jobs will come from.
    demand = Counter(s for j in inst.jobs for s in j.required_skills)
    supply = Counter(s for t in inst.technicians for s in t.skills)
    add("Skills            demand  supply")
    for skill in sorted(set(demand) | set(supply)):
        flag = "  <-- scarce" if supply[skill] and demand[skill] / supply[skill] > 8 else ""
        add(f"  {skill:<18} {demand[skill]:>5}  {supply[skill]:>5}{flag}")
    add("")

    # --- Parts ---
    part_demand = Counter(p for j in inst.jobs for p in j.required_parts)
    part_supply = Counter(p for t in inst.technicians for p in t.van_stock)
    add("Parts             demand  vans carrying")
    for part in sorted(set(part_demand) | set(part_supply)):
        add(f"  {part:<18} {part_demand[part]:>5}  {part_supply[part]:>5}")
    add("")

    # --- Work shape ---
    by_arch = Counter(j.archetype for j in inst.jobs)
    add("Job mix")
    for name, n in by_arch.most_common():
        add(f"  {name:<20} {n:>4}")
    add("")

    by_district = Counter(j.district for j in inst.jobs)
    add("Geography (top 8 districts)")
    for name, n in by_district.most_common(8):
        add(f"  {name:<20} {n:>4}")
    add("")

    # --- Capacity ---
    work_s = sum(j.duration_seconds for j in inst.jobs)
    cap_s = sum(t.shift_seconds for t in inst.technicians)
    ratio = load_ratio(inst)

    windows = Counter()
    for j in inst.jobs:
        span_h = (j.hard_window_end - j.hard_window_start).total_seconds() / 3600
        windows["tight (<4h)" if span_h < 4 else
                "medium (4-8h)" if span_h < 8 else "wide (8h+)"] += 1

    add("Load")
    add(f"  on-site work        {_hhmm(work_s)}")
    add(f"  shift capacity      {_hhmm(cap_s)}")
    add(f"  load ratio          {ratio:.2f}  (work + estimated travel / capacity)")
    add(f"  with pref window    {sum(1 for j in inst.jobs if j.pref_window_start)}"
        f"/{len(inst.jobs)}")
    for label in ("tight (<4h)", "medium (4-8h)", "wide (8h+)"):
        add(f"  {label:<20}{windows[label]:>4}")

    if ratio > 0.95:
        add("")
        add("  NOTE  load ratio is high: expect unassigned jobs. Per the spec")
        add("        that is correct behaviour, not a bug.")
    elif ratio < 0.50:
        add("")
        add("  NOTE  load ratio is low: this day is probably too easy to")
        add("        distinguish a good solver from a mediocre one.")

    # Estimated travel figures are haversine-derived and provisional. Say so
    # here, every time, so no number from this tool is ever mistaken for a
    # benchmark result.
    add("")
    add("  Travel figures above are haversine estimates, not OSRM. They are")
    add("  indicative only and must never be quoted as a result.")

    return "\n".join(lines)


async def _persist(inst: SeedInstance, truncate: bool, jobs_only: bool = False) -> None:
    # Imported lazily so --dry-run works without a reachable database.
    from api.db import SessionFactory
    from data.seed.persist import counts, persist_instance, verify_roundtrip

    async with SessionFactory() as session:
        ids = await persist_instance(
            session, inst, truncate=truncate, jobs_only=jobs_only
        )
        await session.commit()

        problems = await verify_roundtrip(session, inst, ids["jobs"])
        if problems:
            print("\nROUND-TRIP CHECK FAILED", file=sys.stderr)
            for p in problems:
                print(f"  {p}", file=sys.stderr)
            raise SystemExit(1)

        rows = await counts(session)

    print("\nWritten to database")
    for name, n in rows.items():
        print(f"  {name:<14} {n:>5}")
    job_ids = sorted(ids["jobs"].values())
    tech_ids = sorted(ids["technicians"].values())
    print(f"\n  job ids        {job_ids[0]}-{job_ids[-1]}")
    print(f"  technician ids {tech_ids[0]}-{tech_ids[-1]}")
    print("  round-trip coordinate check passed")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m data.seed",
        description="Generate a realistic Klang Valley dispatch day.",
    )
    p.add_argument("--jobs", type=int, default=40)
    p.add_argument("--technicians", type=int, default=8)
    p.add_argument("--seed", type=int, default=42,
                   help="same seed always produces the same day")
    p.add_argument("--day", type=str, default=None,
                   help="YYYY-MM-DD (default: today)")
    p.add_argument("--orphan-jobs", type=int, default=0,
                   help="extra jobs requiring a skill nobody has")
    p.add_argument("--jobs-only", action="store_true",
                   help="add only this day's jobs, reusing the technicians and "
                        "depots already in the database. For seeding a run of "
                        "days that one team works.")
    p.add_argument("--truncate", action="store_true",
                   help="clear existing domain data first")
    p.add_argument("--dry-run", action="store_true",
                   help="generate and print, do not touch the database")
    p.add_argument("--snap", dest="snap", action="store_true", default=True,
                   help="move generated coordinates onto the road network "
                        "using OSRM /nearest (default: on)")
    p.add_argument("--no-snap", dest="snap", action="store_false",
                   help="keep raw generated coordinates. Some will sit in "
                        "parks and water, and OSRM will snap them silently "
                        "on every request instead")
    args = p.parse_args(argv)

    target: date | None = None
    if args.day:
        try:
            target = datetime.strptime(args.day, "%Y-%m-%d").date()
        except ValueError:
            print(f"bad --day {args.day!r}, expected YYYY-MM-DD", file=sys.stderr)
            return 2

    inst = generate_instance(
        n_jobs=args.jobs,
        n_technicians=args.technicians,
        day=target,
        seed=args.seed,
        orphan_jobs=args.orphan_jobs,
    )

    # Snap before validating and before printing: everything downstream
    # should describe the coordinates that will actually be routed from, not
    # the ones the generator happened to draw.
    if args.snap:
        osrm_url = os.environ.get("OSRM_URL", "http://osrm:5000")
        inst, report = asyncio.run(snap_instance(inst, osrm_url))
        print(f"Road snapping ({osrm_url}): {report}")
        if report.moved == 0 and report.unchanged == len(inst.jobs) + len(
            inst.technicians
        ) + len(inst.depots):
            print("  nothing moved -- is OSRM running? "
                  "`docker compose --profile osrm up -d osrm`")
        print()

    print(summarise(inst))

    problems = validate_instance(inst)
    if problems:
        print("\nINSTANCE PROBLEMS")
        for prob in problems:
            print(f"  {prob}")
        if args.orphan_jobs == 0:
            print("\n  These indicate a generator bug. Not written.")
            return 1
    else:
        print("\nInstance checks passed.")

    if args.dry_run:
        print("\n(dry run, nothing written)")
        return 0

    asyncio.run(_persist(inst, args.truncate, args.jobs_only))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
