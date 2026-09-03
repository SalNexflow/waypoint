"""A travel matrix computed once, ahead of time, and shipped as a file.

OSRM is the most expensive thing in this stack to host: a 673MB graph held
resident in memory, served from a persistent volume by a C++ binary. For a
demo deployment that only ever solves a handful of seeded days, that is a lot
of machine to keep running so it can answer the same few thousand questions
over and over.

So: ask OSRM every question in advance, write the answers to a file, and ship
the file. `scripts/freeze_matrix.py` builds one against a live OSRM; this
module reads it back.

Two things make this honest rather than a fudge.

**Provenance travels with the data.** The bundle records the `source` of the
pairs inside it and `FrozenProvider` reports that, rather than hardcoding
"osrm". Freeze a haversine matrix and the bundle says "haversine", every
matrix built from it is `is_reportable == False`, and the benchmark refuses it
exactly as it would refuse live haversine. The provider cannot launder a
number it did not get from OSRM.

**A miss is loud.** The one real risk of a precomputed matrix is that it
quietly stops covering the question being asked -- a job at a new address,
a technician moved house -- and something downstream fills the gap with an
approximation. `TravelMatrix.source` is a single field for the whole matrix,
so a matrix that is 95% frozen OSRM and 5% haversine cannot describe itself
truthfully; there is no per-pair provenance to fall back on. Rather than
degrade the meaning of the field, an uncovered pair raises `UnroutableError`
and names the coordinates it could not find. The demo refuses to answer
instead of answering wrongly.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from routing.base import Coord, TravelMatrix, UnroutableError
from routing.cache import KEY_PLACES, PairCache, _key

log = logging.getLogger("waypoint.routing.frozen")

# Bumped if the on-disk shape changes. A bundle from a future version is
# refused rather than parsed optimistically -- a misread matrix is the silent
# failure this whole package is arranged to prevent.
FORMAT_VERSION = 1


@dataclass(frozen=True)
class FrozenBundle:
    """A precomputed pair set plus the provenance needed to judge it."""

    source: str
    built_at: str
    graph: str
    pairs: dict[str, tuple[int, int]]

    @property
    def size(self) -> int:
        return len(self.pairs)


def write_bundle(
    path: Path,
    cache: PairCache,
    *,
    source: str,
    graph: str,
) -> FrozenBundle:
    """Serialise a warmed PairCache to a bundle file.

    Takes the cache rather than a matrix because warming happens one day at a
    time: the freeze script solves several days against live OSRM and the same
    cache accumulates every pair any of them needed.
    """
    bundle = FrozenBundle(
        source=source,
        built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        graph=graph,
        pairs=dict(cache._data),  # noqa: SLF001 - same package, one writer
    )
    payload = {
        "version": FORMAT_VERSION,
        "source": bundle.source,
        "built_at": bundle.built_at,
        "graph": bundle.graph,
        "pairs": {k: list(v) for k, v in bundle.pairs.items()},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)
    log.info(
        "wrote frozen matrix: %d pairs, source=%s, graph=%s -> %s",
        bundle.size,
        bundle.source,
        bundle.graph,
        path,
    )
    return bundle


def load_bundle(path: Path) -> FrozenBundle:
    """Read a bundle, refusing anything malformed.

    Deliberately strict, and deliberately unlike `PairCache.load`, which
    swallows a corrupt file and starts empty. That is right for a cache, where
    the cost of a bad file is one OSRM round-trip. It is wrong here: an empty
    frozen matrix is not a slow deployment, it is one that cannot answer any
    question at all, and it should fail at startup with a readable reason
    rather than at the first solve with a confusing one.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"no frozen matrix at {path}. Build one against a live OSRM with:\n"
            f"    python -m scripts.freeze_matrix --out {path}"
        )

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"frozen matrix at {path} is unreadable: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"frozen matrix at {path} is not an object")

    version = raw.get("version")
    if version != FORMAT_VERSION:
        raise ValueError(
            f"frozen matrix at {path} is format version {version!r}, "
            f"this build reads version {FORMAT_VERSION}"
        )

    source = raw.get("source")
    if not isinstance(source, str) or not source:
        raise ValueError(
            f"frozen matrix at {path} declares no source. Provenance is not "
            "optional: without it nothing downstream can tell a real road "
            "time from an approximation."
        )

    pairs_raw = raw.get("pairs")
    if not isinstance(pairs_raw, dict) or not pairs_raw:
        raise ValueError(f"frozen matrix at {path} contains no pairs")

    try:
        pairs = {k: (int(v[0]), int(v[1])) for k, v in pairs_raw.items()}
    except (TypeError, ValueError, IndexError, KeyError) as exc:
        raise ValueError(f"frozen matrix at {path} has a malformed pair: {exc}") from exc

    return FrozenBundle(
        source=source,
        built_at=str(raw.get("built_at", "unknown")),
        graph=str(raw.get("graph", "unknown")),
        pairs=pairs,
    )


def cache_from_bundle(bundle: FrozenBundle) -> PairCache:
    """A PairCache preloaded with the bundle and bound to no file.

    `path=None` so nothing ever writes back: the bundle is a build artefact,
    not a cache that grows. On the demo host the filesystem is ephemeral
    anyway, so a write would be lost -- but the stronger reason is that a
    frozen matrix which quietly gained entries at runtime would no longer be
    the thing that was reviewed and shipped.
    """
    cache = PairCache(path=None)
    cache._data = dict(bundle.pairs)  # noqa: SLF001 - same package
    return cache


class FrozenProvider:
    """Answers only what was precomputed; raises on anything else.

    Satisfies `TravelTimeProvider` structurally, and is designed to sit inside
    a `CachingProvider` whose cache was loaded from the same bundle. In that
    arrangement the cache serves every covered matrix and this provider is
    reached *only* when a pair is missing -- so `matrix()` does not compute
    anything. Being called at all is the error condition, and its whole job is
    to say precisely which coordinates were not covered.
    """

    name = "frozen"

    def __init__(self, bundle: FrozenBundle) -> None:
        self.bundle = bundle
        # Read from the file, never hardcoded. See the module docstring.
        self.source = bundle.source

    async def healthy(self) -> bool:
        return self.bundle.size > 0

    async def matrix(self, coords: Sequence[Coord]) -> TravelMatrix:
        pts = [c.rounded(KEY_PLACES) for c in coords]

        missing = 0
        # Points with no pair in the bundle against ANY other point in this
        # request. That is the signature of a genuinely new address, and it is
        # what a dispatcher needs told. A point with *some* pairs present is a
        # different, rarer fault -- a partially warmed bundle -- and saying
        # "this address is unknown" about it would be wrong, so the two cases
        # are counted separately and reported differently.
        found_any: dict[Coord, bool] = {p: False for p in pts}

        for a in pts:
            for b in pts:
                if a == b:
                    continue
                if _key(a, b) in self.bundle.pairs:
                    found_any[a] = True
                    found_any[b] = True
                else:
                    missing += 1

        total = len(pts) * (len(pts) - 1)
        orphans = [p for p in dict.fromkeys(pts) if not found_any[p]]

        if orphans:
            shown = ", ".join(f"({p.lat}, {p.lon})" for p in orphans[:5])
            if len(orphans) > 5:
                shown += f", and {len(orphans) - 5} more"
            what = f"{len(orphans)} location(s) are not in the frozen travel matrix at all: {shown}."
        else:
            what = (
                f"{missing} of {total} pairs are missing, but every location "
                "appears in at least one pair -- the frozen matrix was built "
                "from a different set of locations than this day needs."
            )

        raise UnroutableError(
            f"{what} "
            f"(matrix built {self.bundle.built_at} from {self.bundle.graph}, "
            f"{self.bundle.size} pairs, source={self.source}.) "
            "This deployment has no live routing engine, so no schedule can be "
            "computed for these locations. Rebuild with scripts/freeze_matrix.py "
            "against a live OSRM, or run with ROUTING_PROVIDER=osrm."
        )
