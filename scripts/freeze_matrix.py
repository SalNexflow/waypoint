"""Precompute a travel matrix against a live OSRM and write it to a file.

Run this once, on a machine that has the routing graph, before deploying with
ROUTING_PROVIDER=frozen. It is the only step in the demo deployment that needs
OSRM at all -- after this the graph can go back on the shelf.

    docker compose --profile osrm up -d osrm
    docker compose exec api python -m scripts.freeze_matrix --out data/frozen-matrix.json

What it covers, and why that set:

Every technician home, and every job in the database regardless of status or
day. `repo.load_day` builds its coordinate list from technicians plus the
day's *solvable* jobs, but a re-optimisation loads a different status set and
completions move jobs between statuses as a demo is used. Freezing only what
one solve happens to need today would produce a matrix that stops covering the
day the moment anybody clicks anything. Every job in the database is a few
hundred more coordinates and removes that entire class of surprise.

The output is a single OSRM table request per batch, which is what OSRM is
good at -- it computes the full table in one graph traversal.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import select

from api.config import get_settings
from api.db import SessionFactory
from api.repo import _point
from api.tables import Job, Technician
from routing.base import Coord
from routing.cache import PairCache
from routing.frozen import write_bundle
from routing.osrm import OSRMProvider

log = logging.getLogger("waypoint.freeze")

# OSRM is started with --max-table-size 1000, and an NxN table is N^2 numbers
# to serialise. Batching keeps any single request comfortable and lets a big
# database be frozen without tuning OSRM.
DEFAULT_BATCH = 180


async def collect_coords() -> tuple[list[Coord], int, int]:
    """Every technician home and every job location in the database."""
    async with SessionFactory() as session:
        techs = (
            (await session.execute(select(Technician).order_by(Technician.id)))
            .scalars()
            .all()
        )
        jobs = (
            (await session.execute(select(Job).order_by(Job.id))).scalars().all()
        )

    coords: list[Coord] = []
    for t in techs:
        lat, lon = _point(t.home_location)
        coords.append(Coord(lat, lon))
    for j in jobs:
        lat, lon = _point(j.location)
        coords.append(Coord(lat, lon))

    # Deduplicate but keep order stable, so a rebuild produces the same file
    # when the database has not changed.
    unique = list(dict.fromkeys(c.rounded() for c in coords))
    return unique, len(techs), len(jobs)


async def freeze(out: Path, batch_size: int, graph: str) -> int:
    settings = get_settings()
    osrm = OSRMProvider(settings.osrm_url)

    if not await osrm.healthy():
        log.error(
            "OSRM at %s is not serving. Start it first:\n"
            "    docker compose --profile osrm up -d osrm",
            settings.osrm_url,
        )
        return 1

    coords, n_tech, n_jobs = await collect_coords()
    if len(coords) < 2:
        log.error(
            "only %d distinct locations in the database -- seed a day first",
            len(coords),
        )
        return 1

    log.info(
        "freezing %d distinct locations (%d technicians, %d jobs)",
        len(coords),
        n_tech,
        n_jobs,
    )

    cache = PairCache(path=None)

    # Every pair has to be covered, including across batches, so batches are
    # taken pairwise rather than as a simple partition: block (i, j) is one
    # table request over batch i's points followed by batch j's. With a single
    # batch this is exactly one request, which is the common case.
    blocks = [coords[i : i + batch_size] for i in range(0, len(coords), batch_size)]
    total_requests = len(blocks) * len(blocks)
    done = 0
    max_snap = 0.0

    for a_block in blocks:
        for b_block in blocks:
            if a_block is b_block:
                pts = list(a_block)
            else:
                pts = list(dict.fromkeys(list(a_block) + list(b_block)))

            matrix = await osrm.matrix(pts)
            max_snap = max(max_snap, matrix.max_snap_m)

            problems = matrix.sanity_problems()
            if problems:
                log.error("OSRM returned an unusable matrix: %s", problems[:3])
                return 1

            for i, a in enumerate(pts):
                for j, b in enumerate(pts):
                    if i != j:
                        cache.put(a, b, matrix.durations[i][j], matrix.distances[i][j])

            done += 1
            log.info("  block %d/%d, %d pairs held", done, total_requests, len(cache))

    expected = len(coords) * (len(coords) - 1)
    if len(cache) != expected:
        log.error(
            "expected %d pairs but hold %d -- refusing to write an incomplete "
            "matrix",
            expected,
            len(cache),
        )
        return 1

    bundle = write_bundle(out, cache, source=osrm.source, graph=graph)

    size_mb = out.stat().st_size / 1_048_576
    log.info(
        "done: %d pairs, %.1f MB, worst road snap %.0fm",
        bundle.size,
        size_mb,
        max_snap,
    )
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("data/frozen-matrix.json"),
        help="where to write the bundle (default: data/frozen-matrix.json)",
    )
    ap.add_argument(
        "--batch",
        type=int,
        default=DEFAULT_BATCH,
        help=f"coordinates per OSRM table request (default: {DEFAULT_BATCH})",
    )
    ap.add_argument(
        "--graph",
        default="klang-valley",
        help="name of the OSRM graph, recorded in the bundle for traceability",
    )
    args = ap.parse_args()
    return asyncio.run(freeze(args.out, args.batch, args.graph))


if __name__ == "__main__":
    sys.exit(main())
