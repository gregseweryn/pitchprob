"""Line-latency primitives: how long a bookmaker takes to follow the sharp.

The Kaunitz mechanism says value lives where a slow book has not yet copied
a price the sharp book has already moved. That is a claim about *timing*,
and timing is the one thing the historical dataset cannot answer — two
snapshots per match have no inside. So these are functions over tick series
instead: detect the reference's moves, then ask each follower how long it
took to go the same way.

Three ways this measurement lies, each closed here deliberately:

- **Censoring read as speed.** A book that never follows has no latency, not
  a large one. ``classify_response`` returns ``none`` and the median is
  taken over reactions only, with the reaction rate reported beside it —
  a book that follows 20% of the time in one hour is slower, in the only
  sense that matters, than one that follows 90% of the time in three.
- **Margin changes read as moves.** Callers pass de-margined probabilities,
  not prices, on both sides; a book widening its margin moves every price
  without moving its opinion.
- **Absence read as a reaction.** A follower with no quote from before the
  reference moved has no baseline, so its response is ``unobserved`` and it
  leaves the denominator entirely.

The measurement also has a floor: it cannot resolve delays shorter than the
gap between observations. ``median_gap`` exists so the report can state that
floor and decline to publish a number below it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Literal

Direction = Literal["with", "against", "none", "unobserved"]


@dataclass(frozen=True, slots=True)
class Observation:
    """One de-margined probability for one selection at one instant."""

    at: datetime
    value: float


@dataclass(frozen=True, slots=True)
class Move:
    """A reference-price move worth following."""

    at: datetime
    before: float
    after: float

    @property
    def delta(self) -> float:
        return self.after - self.before


@dataclass(frozen=True, slots=True)
class Response:
    """How one follower answered one reference move."""

    direction: Direction
    latency: timedelta | None


@dataclass(frozen=True, slots=True)
class LatencyStats:
    """Per bookmaker and market. ``observed`` is the denominator: reference
    moves this book had a baseline price for."""

    bookmaker: str
    market: str
    observed: int
    reacted: int
    against: int
    unobserved: int
    median_latency: timedelta | None
    reacted_frac: float | None


def _median(values: Sequence[timedelta]) -> timedelta | None:
    """``statistics.median`` works on timedeltas at runtime but is typed for
    numbers only; the arithmetic is two lines, so do it here."""
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _require_chronological(series: Sequence[Observation]) -> None:
    for earlier, later in pairwise(series):
        if later.at < earlier.at:
            raise ValueError("observations must be in chronological order")


def detect_moves(
    series: Sequence[Observation], *, min_move: float
) -> list[Move]:
    """Consecutive steps of at least ``min_move`` in probability.

    The threshold is not cosmetic: Shin de-margining carries estimation
    noise, so small steps are indistinguishable from the estimator jittering
    and would flood the sample with moves nobody could have traded.
    """
    _require_chronological(series)
    return [
        Move(at=later.at, before=earlier.value, after=later.value)
        for earlier, later in pairwise(series)
        if abs(later.value - earlier.value) >= min_move
    ]


def classify_response(
    move: Move,
    follower: Sequence[Observation],
    *,
    threshold: float,
    deadline: datetime | None = None,
) -> Response:
    """How the follower answered ``move``, and after how long.

    The baseline is the follower's last quote at or before the move — what
    it was still showing when the reference changed its mind. The first
    later quote that differs from that baseline by ``threshold`` decides the
    direction; ``deadline`` (kickoff) ends the window, because a price that
    only moves in-play was never available to bet.
    """
    _require_chronological(follower)
    baseline: float | None = None
    for observation in follower:
        if observation.at <= move.at:
            baseline = observation.value
    if baseline is None:
        return Response(direction="unobserved", latency=None)

    for observation in follower:
        if observation.at <= move.at:
            continue
        if deadline is not None and observation.at > deadline:
            break
        change = observation.value - baseline
        if abs(change) < threshold:
            continue
        same_way = (change > 0) == (move.delta > 0)
        return Response(
            direction="with" if same_way else "against",
            latency=observation.at - move.at,
        )
    return Response(direction="none", latency=None)


def latency_summary(
    responses: Sequence[tuple[str, str, Response]],
) -> list[LatencyStats]:
    """Aggregate ``(bookmaker, market, response)`` triples.

    The median is deliberately taken over reactions only, and is ``None``
    when there are none — an average that silently imputes a value for
    "never followed" is the headline error this whole module exists to
    avoid. Read ``median_latency`` and ``reacted_frac`` together or not
    at all.
    """
    grouped: dict[tuple[str, str], list[Response]] = {}
    for bookmaker, market, response in responses:
        grouped.setdefault((bookmaker, market), []).append(response)

    stats: list[LatencyStats] = []
    for (bookmaker, market), items in sorted(grouped.items()):
        unobserved = sum(1 for r in items if r.direction == "unobserved")
        observed = len(items) - unobserved
        latencies = [
            r.latency for r in items if r.direction == "with" and r.latency
        ]
        stats.append(
            LatencyStats(
                bookmaker=bookmaker,
                market=market,
                observed=observed,
                reacted=len(latencies),
                against=sum(1 for r in items if r.direction == "against"),
                unobserved=unobserved,
                median_latency=_median(latencies),
                reacted_frac=len(latencies) / observed if observed else None,
            )
        )
    return stats


def median_gap(series: Sequence[Observation]) -> timedelta | None:
    """Typical spacing between observations — the resolution floor.

    No latency shorter than this is measurable, so a report whose sources
    are daily snapshots must say "24h" here and stop, rather than publish a
    median that is really an artefact of when the recorder happened to run.
    """
    return _median([later.at - earlier.at for earlier, later in pairwise(series)])
