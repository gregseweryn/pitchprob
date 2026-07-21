"""Read-model over the odds tape: "the Pinnacle fair as of an instant".

The tape (``odds_ticks``, ADR 0012) is append-only and unjudged; this module
is where judgment happens. The scanner and the pick ledger both anchor
real-money decisions on it, so the one invariant that matters is *no
lookahead*: ``fair_at`` returns the latest **complete** selection set at or
before the asked-for instant, never a later one, and ``closing_fair`` stops
strictly before kickoff. Every quote carries its ``observed_at`` — the tape
snapshots daily, so the anchor can be hours old and honesty requires saying
so (freshness is payload, not metadata).

Ticks are filtered in SQL by the indexed ``event_id`` and grouped in Python:
one event's history is tens of rows, and Python-side grouping keeps the
datetime comparisons backend-portable (SQLite returns naive UTC datetimes,
Postgres keeps the offset).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from pitchprob.betting.odds_math import remove_overround_shin
from pitchprob.data.orm import OddsTick

#: Selection sets that make a market complete enough to de-margin.
MARKET_SELECTIONS: dict[str, tuple[str, ...]] = {
    "1x2": ("home", "draw", "away"),
    "ou": ("over", "under"),
    "ah": ("home", "away"),
    # Corners (ADR 0014). Same two shapes as the goals markets, so every
    # read-model below — completeness, main-line choice, Shin de-margin —
    # applies unchanged. What differs is coverage, not geometry: corners
    # are only on the tape from roughly a day before kickoff.
    "corners_ou": ("over", "under"),
    "corners_ah": ("home", "away"),
}

SHARP_BOOKMAKER = "pinnacle"


@dataclass(frozen=True, slots=True)
class EventInfo:
    event_id: str
    sport_key: str
    home_team: str
    away_team: str
    commence_time: datetime


@dataclass(frozen=True, slots=True)
class FairQuote:
    """One complete de-margined market from a single tape snapshot."""

    event_id: str
    home_team: str
    away_team: str
    commence_time: datetime
    bookmaker: str
    market: str
    line: Decimal | None
    prices: dict[str, Decimal]
    probabilities: dict[str, float]
    observed_at: datetime


def as_utc(value: datetime) -> datetime:
    """SQLite drops the offset; stored values are UTC by construction."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _validate_market(market: str) -> tuple[str, ...]:
    if market not in MARKET_SELECTIONS:
        known = ", ".join(sorted(MARKET_SELECTIONS))
        raise ValueError(f"unknown market {market!r} (known: {known})")
    return MARKET_SELECTIONS[market]


#: Markets whose bets carry a line; 1X2 must not.
LINE_MARKETS = frozenset({"ou", "ah", "corners_ou", "corners_ah"})


def validate_market_selection(
    market: str, selection: str, line: Decimal | None
) -> None:
    """Shared bet-shape validation for the ledger and the scanner."""
    selections = _validate_market(market)
    if selection not in selections:
        raise ValueError(
            f"selection {selection!r} is not valid for market {market!r} "
            f"(valid: {', '.join(selections)})"
        )
    if market in LINE_MARKETS and line is None:
        raise ValueError(f"market {market!r} requires a line")
    if market not in LINE_MARKETS and line is not None:
        raise ValueError(f"market {market!r} takes no line, got {line}")


def _complete_lines(
    ticks: list[OddsTick], selections: tuple[str, ...]
) -> dict[Decimal | None, dict[str, OddsTick]]:
    """Lines within one snapshot that carry every required selection."""
    by_line: dict[Decimal | None, dict[str, OddsTick]] = {}
    for tick in ticks:
        by_line.setdefault(tick.line, {})[tick.selection] = tick
    return {
        line: sels
        for line, sels in by_line.items()
        if all(name in sels for name in selections)
    }


def _most_balanced(
    complete: dict[Decimal | None, dict[str, OddsTick]],
    selections: tuple[str, ...],
) -> Decimal | None:
    """The main line: the one whose prices sit closest together."""

    def spread(line: Decimal | None) -> Decimal:
        prices = [complete[line][name].price for name in selections]
        return max(prices) - min(prices)

    return min(complete, key=spread)


def fair_at(
    session: Session,
    *,
    event_id: str,
    market: str,
    line: Decimal | None = None,
    at: datetime | None = None,
    bookmaker: str = SHARP_BOOKMAKER,
) -> FairQuote | None:
    """Latest complete Shin-fair market at or before ``at`` (None: latest).

    With ``line`` given, the latest complete snapshot must quote exactly
    that line — comparing a Polish book's 2.5 line against a Pinnacle 3.0
    fair would be an anchor error, and falling back to an *older* snapshot
    still quoting the line the market has since abandoned would be a stale
    anchor, so both cases return None rather than approximating. With
    ``line`` None the snapshot's most balanced (main) line is used.
    """
    selections = _validate_market(market)
    rows = list(
        session.execute(
            select(OddsTick).where(
                OddsTick.event_id == event_id,
                OddsTick.market == market,
                OddsTick.bookmaker == bookmaker,
            )
        )
        .scalars()
        .all()
    )
    by_snapshot: dict[datetime, list[OddsTick]] = {}
    for tick in rows:
        when = as_utc(tick.observed_at)
        if at is not None and when > at:
            continue
        by_snapshot.setdefault(when, []).append(tick)
    for when in sorted(by_snapshot, reverse=True):
        complete = _complete_lines(by_snapshot[when], selections)
        if not complete:
            continue
        chosen: Decimal | None
        if line is not None:
            if line not in complete:
                return None
            chosen = line
        else:
            chosen = _most_balanced(complete, selections)
        chosen_ticks = complete[chosen]
        prices = {name: chosen_ticks[name].price for name in selections}
        fair = remove_overround_shin([float(prices[name]) for name in selections])
        first = chosen_ticks[selections[0]]
        return FairQuote(
            event_id=first.event_id,
            home_team=first.home_team,
            away_team=first.away_team,
            commence_time=as_utc(first.commence_time),
            bookmaker=bookmaker,
            market=market,
            line=chosen,
            prices=prices,
            probabilities=dict(zip(selections, fair, strict=True)),
            observed_at=when,
        )
    return None


def closing_fair(
    session: Session,
    *,
    event_id: str,
    market: str,
    line: Decimal | None = None,
    bookmaker: str = SHARP_BOOKMAKER,
) -> FairQuote | None:
    """Last complete pre-kickoff snapshot — the tape's closing line.

    The daily tape means "closing" can be hours before kickoff; the
    returned ``observed_at`` states exactly how far, and CLV consumers must
    surface it rather than pretend a T-0 close.
    """
    commence = session.execute(
        select(OddsTick.commence_time)
        .where(OddsTick.event_id == event_id)
        .limit(1)
    ).scalar_one_or_none()
    if commence is None:
        return None
    kickoff = as_utc(commence)
    quote = fair_at(
        session,
        event_id=event_id,
        market=market,
        line=line,
        at=kickoff,
        bookmaker=bookmaker,
    )
    if quote is None or quote.observed_at >= kickoff:
        return None
    return quote


def find_events(session: Session, query: str, *, at: datetime) -> list[EventInfo]:
    """Upcoming tape events whose team names contain ``query`` (case-blind)."""
    needle = query.strip().lower()
    rows = session.execute(
        select(
            OddsTick.event_id,
            OddsTick.sport_key,
            OddsTick.home_team,
            OddsTick.away_team,
            OddsTick.commence_time,
        ).distinct()
    ).all()
    events: dict[str, EventInfo] = {}
    for event_id, sport_key, home, away, commence in rows:
        kickoff = as_utc(commence)
        if kickoff <= at:
            continue
        if needle not in home.lower() and needle not in away.lower():
            continue
        events[event_id] = EventInfo(
            event_id=event_id,
            sport_key=sport_key,
            home_team=home,
            away_team=away,
            commence_time=kickoff,
        )
    return sorted(events.values(), key=lambda e: (e.commence_time, e.event_id))
