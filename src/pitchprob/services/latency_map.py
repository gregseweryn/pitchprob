"""The PL line-latency map: which book follows the sharp last (ADR 0014).

The audit put this at P1 and called it measurable from the existing tape.
It is not, and the report says so out loud rather than filling the gap with
a number. Two facts block it: The Odds API carries no Polish-licensed book
(ADR 0012 — the tape sees `betclic_fr`, never Betclic PL), and the tape
snapshots once a day, which quantises every delay to 24 hours. So this
service is written source-agnostic: it reads ``odds_ticks`` regardless of
which feed wrote them, and the odds-api.io feed — 100 requests an hour, six
Polish books in its catalogue — is what will eventually make it say
something. Until then, "N=0, and here is which precondition failed" is the
correct output, not an error.

Method, in one line: de-margin every book's own market at every snapshot,
detect the reference's moves, ask each other book how long it took to go the
same way. The primitives and the ways this can lie are in
``evaluation.latency``; this module only assembles series and renders.

Two limitations stated rather than hidden. Series are built per (market,
line), so a book that responds by **moving its line** instead of its price
reads as no reaction — for OU and AH that understates responsiveness, which
is why 1X2 is the market to trust here. And a book that is absent from a
snapshot has no baseline for that move and leaves the denominator, so
coverage gaps cannot masquerade as slowness.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pitchprob.betting.odds_math import remove_overround_shin
from pitchprob.data.orm import OddsTick
from pitchprob.evaluation.latency import (
    LatencyStats,
    Observation,
    Response,
    classify_response,
    detect_moves,
    latency_summary,
    median_gap,
)
from pitchprob.services.tape import MARKET_SELECTIONS, SHARP_BOOKMAKER, as_utc

#: The selection whose probability represents a market's move. One is
#: enough: the selections of a de-margined market sum to one, so they move
#: together, and picking one keeps a single move from being counted twice.
CANONICAL_SELECTION = {
    "1x2": "home",
    "ou": "over",
    "ah": "home",
    "corners_ou": "over",
    "corners_ah": "home",
}

DEFAULT_MARKETS = ("1x2", "ou", "ah")

#: Below this, Shin estimation noise and a daily tape's quantisation are
#: indistinguishable from a real move.
DEFAULT_MIN_MOVE = 0.02

#: Reference moves a book must be observable for before its median means
#: anything. Chosen before any data exists, so it cannot be tuned to a
#: result later.
DEFAULT_MIN_OBSERVED = 30


@dataclass(slots=True)
class LatencyMapResult:
    metrics: dict[str, Any]
    report: str


_SeriesKey = tuple[str, str, Decimal | None, str]


def _probability_series(
    ticks: Sequence[OddsTick], selections: tuple[str, ...], canonical: str
) -> list[Observation]:
    """De-margined probability of the canonical selection, per snapshot.

    Incomplete snapshots are dropped: half a market cannot be de-margined,
    and guessing the missing side would invent the very movement we are
    trying to measure.
    """
    by_snapshot: dict[datetime, dict[str, OddsTick]] = {}
    for tick in ticks:
        by_snapshot.setdefault(as_utc(tick.observed_at), {})[tick.selection] = tick
    series: list[Observation] = []
    for when in sorted(by_snapshot):
        quotes = by_snapshot[when]
        if not all(name in quotes for name in selections):
            continue
        fair = remove_overround_shin(
            [float(quotes[name].price) for name in selections]
        )
        series.append(
            Observation(at=when, value=fair[selections.index(canonical)])
        )
    return series


def run_latency_map(
    session: Session,
    *,
    reference: str = SHARP_BOOKMAKER,
    source: str | None = None,
    since: datetime | None = None,
    markets: Sequence[str] = DEFAULT_MARKETS,
    min_move: float = DEFAULT_MIN_MOVE,
    threshold: float = DEFAULT_MIN_MOVE,
    min_observed: int = DEFAULT_MIN_OBSERVED,
) -> LatencyMapResult:
    """Measure how long each book takes to follow ``reference``."""
    query = select(OddsTick).where(OddsTick.market.in_(list(markets)))
    if source is not None:
        query = query.where(OddsTick.source == source)
    if since is not None:
        query = query.where(OddsTick.observed_at >= since)
    rows = list(session.execute(query).scalars().all())

    grouped: dict[_SeriesKey, list[OddsTick]] = {}
    kickoffs: dict[str, datetime] = {}
    for tick in rows:
        grouped.setdefault(
            (tick.event_id, tick.market, tick.line, tick.bookmaker), []
        ).append(tick)
        kickoffs[tick.event_id] = as_utc(tick.commence_time)

    series: dict[_SeriesKey, list[Observation]] = {}
    for key, ticks in grouped.items():
        selections = MARKET_SELECTIONS[key[1]]
        built = _probability_series(
            ticks, selections, CANONICAL_SELECTION[key[1]]
        )
        if built:
            series[key] = built

    responses: list[tuple[str, str, Response]] = []
    reference_moves = 0
    reference_series: list[list[Observation]] = []
    for (event_id, market, line, bookmaker), observations in series.items():
        if bookmaker != reference:
            continue
        reference_series.append(observations)
        moves = detect_moves(observations, min_move=min_move)
        reference_moves += len(moves)
        if not moves:
            continue
        followers = {
            key[3]: obs
            for key, obs in series.items()
            if key[:3] == (event_id, market, line) and key[3] != reference
        }
        for move in moves:
            for follower, follower_series in followers.items():
                responses.append((
                    follower,
                    market,
                    classify_response(
                        move,
                        follower_series,
                        threshold=threshold,
                        deadline=kickoffs[event_id],
                    ),
                ))

    stats = latency_summary(responses)
    gaps = [gap for s in reference_series if (gap := median_gap(s)) is not None]
    floor = (
        sorted(gaps)[len(gaps) // 2].total_seconds() / 3600 if gaps else None
    )
    sufficient = bool(stats) and all(
        s.observed >= min_observed for s in stats
    )

    metrics: dict[str, Any] = {
        "study": "latency-map",
        "reference": reference,
        "source": source or "(all)",
        "ticks": len(rows),
        "events": len(kickoffs),
        "bookmakers": sorted({t.bookmaker for t in rows}),
        "reference_moves": reference_moves,
        "resolution_floor_hours": floor,
        "min_move": min_move,
        "min_observed": min_observed,
        "sufficient": sufficient,
        "books": [_book_row(s) for s in stats],
    }
    return LatencyMapResult(metrics=metrics, report=_render(metrics))


def _book_row(stats: LatencyStats) -> dict[str, Any]:
    return {
        "bookmaker": stats.bookmaker,
        "market": stats.market,
        "observed": stats.observed,
        "reacted": stats.reacted,
        "against": stats.against,
        "unobserved": stats.unobserved,
        "reacted_frac": stats.reacted_frac,
        "median_latency_hours": (
            stats.median_latency.total_seconds() / 3600
            if stats.median_latency is not None
            else None
        ),
    }


def _preconditions(metrics: dict[str, Any]) -> list[str]:
    """Why there is nothing to report — named precisely, because each cause
    has a different fix."""
    failures: list[str] = []
    if metrics["ticks"] == 0:
        failures.append(
            "no ticks on the tape for the requested source and window"
        )
        return failures
    if metrics["reference"] not in metrics["bookmakers"]:
        failures.append(
            f"the reference book {metrics['reference']!r} is not on this tape "
            f"(seen: {', '.join(metrics['bookmakers'])})"
        )
    if metrics["resolution_floor_hours"] is None:
        failures.append(
            "the reference has only a single snapshot — a latency needs a "
            "series, and one observation is not one"
        )
    elif metrics["reference_moves"] == 0:
        failures.append(
            f"the reference never moved by {metrics['min_move']:.1%} or more"
        )
    return failures


def _render(metrics: dict[str, Any]) -> str:
    lines = [
        f"# Line-latency map — reference {metrics['reference']}, "
        f"source {metrics['source']}",
        "",
        f"- Tape: {metrics['ticks']} ticks over {metrics['events']} events; "
        f"books seen: {', '.join(metrics['bookmakers']) or '(none)'}",
        f"- Reference moves of >= {metrics['min_move']:.1%}: "
        f"{metrics['reference_moves']}",
    ]
    floor = metrics["resolution_floor_hours"]
    lines.append(
        "- Resolution floor (median gap between reference snapshots): "
        + (
            f"**{floor:.1f}h** — no delay shorter than this is measurable"
            if floor is not None
            else "not defined (fewer than two snapshots)"
        )
    )
    lines.append("")

    failures = _preconditions(metrics)
    if failures:
        lines += ["## No measurement", ""]
        lines += [f"- {failure}" for failure in failures]
        lines += [
            "",
            "This is the expected output while the tape carries one daily",
            "snapshot and no Polish-licensed book. The fix is a denser feed",
            "that quotes PL books, not a change to this analysis.",
        ]
        return "\n".join(lines)

    lines += [
        "| bookmaker | market | moves observed | reacted | against | "
        "reaction rate | median delay |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in metrics["books"]:
        rate = row["reacted_frac"]
        median = row["median_latency_hours"]
        lines.append(
            f"| {row['bookmaker']} | {row['market']} | {row['observed']} "
            f"| {row['reacted']} | {row['against']} "
            f"| {'—' if rate is None else f'{rate:.0%}'} "
            f"| {'never followed' if median is None else f'{median:.1f}h'} |"
        )
    lines += [
        "",
        "Read the delay and the reaction rate together: a median over the",
        "times a book *did* follow says nothing about the times it did not,",
        "and the bets live in the gap between them.",
    ]
    if not metrics["sufficient"]:
        lines += [
            "",
            f"**This is not a finding.** At least one book is below the "
            f"{metrics['min_observed']}-move evidence bar set before the data "
            "existed. Treat the table as a smoke test of the instrument.",
        ]
    return "\n".join(lines)
