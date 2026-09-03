"""The travel-time interface every provider implements.

Two implementations exist: OSRM (real road network) and haversine (great-circle
with a fudge factor). The solver only ever sees this interface, so solver work
is never blocked on OSRM being built, and the benchmark can prove the two are
being compared on equal terms.

Crucially, a matrix carries its own provenance. `source` travels with the data
so nothing downstream can accidentally publish a haversine-derived figure as a
result -- see `is_reportable`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple, Protocol, runtime_checkable


class Coord(NamedTuple):
    """A geographic point, latitude first.

    A NamedTuple, not a bare tuple, specifically to defend against the lat/lon
    ordering bug. `Coord(3.15, 101.71)` reads unambiguously and `c.lat` cannot
    silently be the longitude. Providers that need the other order (OSRM's URL
    format is lon,lat) do the flip in exactly one place, close to the wire.

    NamedTuple is still a tuple, so it unpacks and compares like one and costs
    nothing extra to build -- relevant when a benchmark constructs millions.
    """

    lat: float
    lon: float

    def rounded(self, places: int = 6) -> Coord:
        """Snap to a grid for stable cache keys. 6dp is about 0.1m."""
        return Coord(round(self.lat, places), round(self.lon, places))


class UnroutableError(RuntimeError):
    """Raised when a provider cannot produce a duration for some pair.

    This exists to make a specific silent failure loud. OSRM returns `null` in
    the matrix for a pair it cannot route -- typically a coordinate outside the
    extract's bounding box. If that null were quietly coerced to 0, the solver
    would believe two cities are adjacent, produce a beautiful impossible
    schedule, and nothing about the output would look wrong.
    """


@dataclass(frozen=True)
class TravelMatrix:
    """Durations and distances between an ordered list of coordinates.

    durations[i][j] is seconds to drive from coords[i] to coords[j].
    The matrix is asymmetric: one-way streets and turn restrictions mean
    A->B and B->A genuinely differ. Nothing may assume durations[i][j] ==
    durations[j][i].
    """

    coords: tuple[Coord, ...]
    durations: tuple[tuple[int, ...], ...]  # seconds
    distances: tuple[tuple[int, ...], ...]  # metres
    source: str                             # "osrm" | "haversine"
    # Largest distance any input coordinate was moved to reach a road, in
    # metres. Only OSRM reports this. A large value means a job address is
    # nowhere near the road network, which is worth knowing before blaming
    # the solver for a strange route.
    max_snap_m: float = 0.0

    @property
    def is_reportable(self) -> bool:
        """Whether figures derived from this matrix may be published.

        Only OSRM counts. Haversine is a development convenience: it
        understates real Klang Valley driving by roughly a third, and any
        benchmark built on it would be fiction. The phase 10 harness refuses
        to print a headline improvement unless this is True.
        """
        return self.source == "osrm"

    @property
    def size(self) -> int:
        return len(self.coords)

    def duration(self, i: int, j: int) -> int:
        return self.durations[i][j]

    def distance(self, i: int, j: int) -> int:
        return self.distances[i][j]

    def sanity_problems(self) -> list[str]:
        """Structural checks any correct matrix must pass.

        Called after every fetch. The spec is explicit that a wrong travel
        matrix is a silent failure that makes every schedule wrong while
        looking fine, so the matrix is checked rather than trusted -- the same
        instinct as the phase 5 feasibility checker.
        """
        problems: list[str] = []
        n = len(self.coords)

        if len(self.durations) != n:
            problems.append(f"durations has {len(self.durations)} rows, expected {n}")
            return problems

        for i, row in enumerate(self.durations):
            if len(row) != n:
                problems.append(f"durations row {i} has {len(row)} entries, expected {n}")
                continue
            if row[i] != 0:
                problems.append(f"durations[{i}][{i}] is {row[i]}, expected 0")
            for j, v in enumerate(row):
                if v < 0:
                    problems.append(f"durations[{i}][{j}] is negative ({v})")

        # A 20-hour drive inside the Klang Valley means the coordinate set has
        # something in it that does not belong.
        worst = max((v for row in self.durations for v in row), default=0)
        if worst > 6 * 3600:
            problems.append(
                f"longest duration is {worst / 3600:.1f}h, which is implausible "
                "for the Klang Valley"
            )

        return problems


@runtime_checkable
class TravelTimeProvider(Protocol):
    """Structural interface for anything that can produce a travel matrix.

    A Protocol is Python's structural typing: any class with a matching
    `name`, `source` and `matrix()` satisfies it, with no base class and no
    registration. If you know TypeScript, this is the same idea as an
    `interface` -- conformance is by shape, checked statically. It is the
    opposite of an abc.ABC, where a class only counts if it explicitly
    inherits.

    Structural is the right choice here because it keeps the providers
    independent: `HaversineProvider` does not import anything from `osrm.py`,
    and a test double is just a small class with the right methods.

    `@runtime_checkable` additionally allows `isinstance(x, TravelTimeProvider)`
    at run time -- though note it only checks that the attribute names exist,
    not their signatures.
    """

    name: str
    source: str

    async def matrix(self, coords: Sequence[Coord]) -> TravelMatrix: ...

    async def healthy(self) -> bool: ...
