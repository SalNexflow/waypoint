"""Matrix cache: same coordinates, same answer.

Caches individual **pairs**, not whole matrices. Keying on the full
coordinate set would be simpler, but it invalidates the moment one job is
added or removed -- which is exactly what happens on every re-optimisation in
phase 9. Pair-level caching means a re-solve over 39 of the same 40 jobs is
served entirely from memory.

Two levels: a dict for this process, and a JSON file so the cache survives
container restarts (uvicorn --reload restarts constantly during development,
and the phase 10 benchmark re-solves the same instances repeatedly).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path

from routing.base import Coord, TravelMatrix, TravelTimeProvider

log = logging.getLogger("waypoint.routing.cache")

# Coordinates are rounded before becoming a key. 6dp is ~0.1m, far below the
# precision at which OSRM's road snapping would give a different answer, and
# it means float noise cannot produce a cache miss for the same place.
KEY_PLACES = 6

# Guard against a benchmark run growing the file without bound. 500k pairs is
# roughly a 700-coordinate matrix, well beyond anything this project solves.
DEFAULT_MAX_ENTRIES = 500_000


def _key(a: Coord, b: Coord) -> str:
    a, b = a.rounded(KEY_PLACES), b.rounded(KEY_PLACES)
    return f"{a.lat},{a.lon}|{b.lat},{b.lon}"


class PairCache:
    """Directional pair store. (a -> b) and (b -> a) are separate entries,
    because real road networks are asymmetric."""

    def __init__(
        self,
        path: Path | None = None,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self.path = path
        self.max_entries = max_entries
        self._data: dict[str, tuple[int, int]] = {}
        self.hits = 0
        self.misses = 0
        if path is not None:
            self.load()

    def load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = {k: (int(v[0]), int(v[1])) for k, v in raw.items()}
            log.info("routing cache loaded %d pairs from %s", len(self._data), self.path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # A corrupt cache must never take the app down. Losing it costs
            # one OSRM round-trip.
            log.warning("routing cache at %s unreadable (%s); starting empty",
                        self.path, exc)
            self._data = {}

    def save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._data), encoding="utf-8")
            # Atomic replace, so an interrupted write cannot leave a
            # half-written file that fails to parse on next start.
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("could not save routing cache to %s: %s", self.path, exc)

    def get(self, a: Coord, b: Coord) -> tuple[int, int] | None:
        v = self._data.get(_key(a, b))
        if v is None:
            self.misses += 1
        else:
            self.hits += 1
        return v

    def put(self, a: Coord, b: Coord, duration_s: int, distance_m: int) -> None:
        if len(self._data) >= self.max_entries:
            return
        self._data[_key(a, b)] = (duration_s, distance_m)

    def __len__(self) -> int:
        return len(self._data)

    @property
    def stats(self) -> dict[str, int | float]:
        total = self.hits + self.misses
        return {
            "entries": len(self._data),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
        }


class CachingProvider:
    """Wraps any TravelTimeProvider with a pair cache.

    Satisfies TravelTimeProvider itself, so callers cannot tell the difference
    and nothing downstream needs to know caching exists. It also forwards
    `source` from the wrapped provider rather than inventing its own -- a
    cached haversine matrix is still haversine, and still not reportable.
    """

    def __init__(
        self,
        inner: TravelTimeProvider,
        cache: PairCache | None = None,
        autosave: bool = True,
    ) -> None:
        self.inner = inner
        self.cache = cache if cache is not None else PairCache()
        self.autosave = autosave

    @property
    def name(self) -> str:
        return f"cached({self.inner.name})"

    @property
    def source(self) -> str:
        return self.inner.source

    async def healthy(self) -> bool:
        return await self.inner.healthy()

    async def matrix(self, coords: Sequence[Coord]) -> TravelMatrix:
        pts = tuple(c.rounded(KEY_PLACES) for c in coords)
        n = len(pts)

        # Try to assemble the whole matrix from cached pairs.
        durations: list[list[int]] = []
        distances: list[list[int]] = []
        complete = True

        for a in pts:
            d_row: list[int] = []
            m_row: list[int] = []
            for b in pts:
                if a == b:
                    d_row.append(0)
                    m_row.append(0)
                    continue
                hit = self.cache.get(a, b)
                if hit is None:
                    complete = False
                    break
                d_row.append(hit[0])
                m_row.append(hit[1])
            if not complete:
                break
            durations.append(d_row)
            distances.append(m_row)

        if complete and n > 0:
            log.debug("routing cache served a %dx%d matrix in full", n, n)
            return TravelMatrix(
                coords=pts,
                durations=tuple(tuple(r) for r in durations),
                distances=tuple(tuple(r) for r in distances),
                source=self.inner.source,
            )

        # Any miss means fetching the whole matrix. One table request for
        # N^2 pairs is far cheaper than assembling sub-requests, and OSRM
        # computes the full table in a single graph traversal regardless.
        matrix = await self.inner.matrix(pts)

        for i, a in enumerate(pts):
            for j, b in enumerate(pts):
                if i != j:
                    self.cache.put(a, b, matrix.durations[i][j], matrix.distances[i][j])

        if self.autosave:
            self.cache.save()

        return matrix
