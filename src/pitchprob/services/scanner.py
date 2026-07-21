"""The PL value scanner: "graj / nie graj + o ile" (Phase 5 part 3).

The operator types in the prices Polish books are showing for one selection;
the scanner verdicts each against the **live Pinnacle fair from the tape**
(primary — the sharp anchor *is* the edge thesis; per the Phase 0-2a
verdicts the model does not outpredict the market) and, optionally, a model
fair (secondary, informational — it never flips a verdict). Comparisons run
on *effective* prices: x0.88 for taxed PL books, x1.0 under a tax-free
promo, boosted quotes priced as instruments via ``promo_ev``.

Expect mostly "NO BET": the 12% turnover tax sits in the prices. Value
appears in boosts/promos and in slow-moving PL prices after the sharp line
has moved — which is exactly what the anchor comparison detects.

Freshness is part of the verdict: the tape snapshots daily, so the anchor
can be hours old. ``anchor_age`` is always reported, and an anchor older
than ``max_anchor_age`` downgrades PLAY to STALE — a stale sharp line is
not a betting basis, it is a prompt to refresh the tape.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from pitchprob.betting.effective import PromoEvaluation, PromoTerms, promo_ev
from pitchprob.data.adapters.odds_api_io import SOURCE as FEED_SOURCE
from pitchprob.data.normalize import canonical_team_name, odds_api_canonical
from pitchprob.data.orm import OddsTick
from pitchprob.services.tape import (
    FairQuote,
    as_utc,
    fair_at,
    find_events,
    validate_market_selection,
)

Verdict = Literal["PLAY", "NO BET", "STALE", "NO ANCHOR", "UNVERIFIED"]

#: Where a quote came from. ``feed`` is the odds-api.io PL feed (ADR 0014),
#: and until ``quote_check`` clears it against the operator's own screen it
#: cannot produce a PLAY on its own — an automatic price that nobody has
#: verified is a lead, not a bet.
QuoteSource = Literal["operator", "feed"]

#: Minimum primary edge before the scanner says PLAY. Below ~2% the anchor
#: error dominates: the tape is a daily snapshot and Shin fair carries
#: estimation noise, so smaller edges are indistinguishable from staleness.
DEFAULT_MIN_EDGE = 0.02

#: One daily snapshot plus slack: an older anchor means the tape missed a
#: day, and a verdict built on it would be a guess.
DEFAULT_MAX_ANCHOR_AGE = timedelta(hours=30)


@dataclass(frozen=True, slots=True)
class OperatorQuote:
    """One price the operator sees at a Polish book, with its promo terms."""

    bookmaker: str
    price: Decimal
    promo: PromoTerms | None = None
    source: QuoteSource = "operator"


@dataclass(frozen=True, slots=True)
class QuoteVerdict:
    """One book's quote, priced and judged against the anchors."""

    bookmaker: str
    price_quoted: Decimal
    promo: PromoTerms | None
    source: QuoteSource
    evaluation: PromoEvaluation | None
    edge: float | None
    edge_model: float | None
    verdict: Verdict


@dataclass(frozen=True, slots=True)
class ScanResult:
    event_id: str
    home_team: str
    away_team: str
    commence_time: datetime
    market: str
    line: Decimal | None
    anchor: FairQuote | None
    anchor_age: timedelta | None
    model_probability: float | None
    verdicts: list[QuoteVerdict]


def _resolve_event(
    session: Session, *, query: str | None, event_id: str | None, at: datetime
) -> tuple[str, str, str, datetime]:
    """(event_id, home, away, kickoff) — from an id or an unambiguous query."""
    if event_id is not None:
        row = session.execute(
            select(
                OddsTick.home_team, OddsTick.away_team, OddsTick.commence_time
            )
            .where(OddsTick.event_id == event_id)
            .limit(1)
        ).first()
        if row is None:
            raise ValueError(f"event {event_id!r} is not on the tape")
        return event_id, row[0], row[1], as_utc(row[2])
    if query is None:
        raise ValueError("either query or event_id is required")
    events = find_events(session, query, at=at)
    if not events:
        raise ValueError(f"no upcoming tape event matches {query!r}")
    if len(events) > 1:
        names = "; ".join(
            f"{e.home_team} vs {e.away_team} ({e.event_id})" for e in events
        )
        raise ValueError(f"query {query!r} is ambiguous: {names}")
    event = events[0]
    return (
        event.event_id,
        event.home_team,
        event.away_team,
        event.commence_time,
    )


def feed_quotes(
    session: Session,
    *,
    home_team: str,
    away_team: str,
    kickoff: datetime,
    market: str,
    selection: str,
    line: Decimal | None,
    at: datetime,
) -> list[OperatorQuote]:
    """Latest odds-api.io quotes for this selection, one per book.

    The two feeds name events differently, so fixtures are joined on
    canonical team names and a kickoff within a day — the same rule the
    ledger uses, and with the same discipline: a fixture that does not match
    yields nothing rather than a guess. Every quote comes back marked
    ``source="feed"``, which is what keeps it out of a PLAY verdict.
    """
    home = canonical_team_name(odds_api_canonical(home_team))
    away = canonical_team_name(odds_api_canonical(away_team))
    rows = session.execute(
        select(OddsTick)
        .where(
            OddsTick.source == FEED_SOURCE,
            OddsTick.market == market,
            OddsTick.selection == selection,
            OddsTick.commence_time >= kickoff - timedelta(days=1),
            OddsTick.commence_time <= kickoff + timedelta(days=1),
        )
        .order_by(OddsTick.observed_at.desc())
    ).scalars()

    latest: dict[str, OddsTick] = {}
    for tick in rows:
        if as_utc(tick.observed_at) > at:
            continue
        if tick.line != line:
            continue
        if canonical_team_name(odds_api_canonical(tick.home_team)) != home:
            continue
        if canonical_team_name(odds_api_canonical(tick.away_team)) != away:
            continue
        latest.setdefault(tick.bookmaker, tick)
    return [
        OperatorQuote(bookmaker=book, price=tick.price, source="feed")
        for book, tick in sorted(latest.items())
    ]


def _judge(
    edge: float | None,
    *,
    anchored: bool,
    fresh: bool,
    min_edge: float,
    source: QuoteSource = "operator",
) -> Verdict:
    if not anchored or edge is None:
        return "NO ANCHOR"
    if edge < min_edge:
        return "NO BET"
    if not fresh:
        return "STALE"
    # An unvalidated feed price may flag an opportunity but may not be the
    # basis of a bet: go and look at the book's own screen first.
    return "PLAY" if source == "operator" else "UNVERIFIED"


def scan(
    session: Session,
    *,
    market: str,
    selection: str,
    quotes: list[OperatorQuote],
    line: Decimal | None = None,
    query: str | None = None,
    event_id: str | None = None,
    model_probability: float | None = None,
    now: datetime | None = None,
    min_edge: float = DEFAULT_MIN_EDGE,
    max_anchor_age: timedelta = DEFAULT_MAX_ANCHOR_AGE,
) -> ScanResult:
    """Verdict every operator quote for one selection of one fixture.

    For ou/ah the operator's ``line`` must equal the tape's current line —
    a Pinnacle fair for a different line is not an anchor (NO ANCHOR beats
    a wrong comparison). The model probability, when supplied, adds an
    informational ``edge_model`` per quote and never changes a verdict.
    """
    validate_market_selection(market, selection, line)
    when = now if now is not None else datetime.now(tz=UTC)
    resolved_id, home, away, kickoff = _resolve_event(
        session, query=query, event_id=event_id, at=when
    )
    anchor = fair_at(
        session, event_id=resolved_id, market=market, line=line, at=when
    )
    anchor_age = when - anchor.observed_at if anchor is not None else None
    fresh = anchor_age is not None and anchor_age <= max_anchor_age
    fair = anchor.probabilities[selection] if anchor is not None else None

    verdicts: list[QuoteVerdict] = []
    for quote in quotes:
        evaluation = (
            promo_ev(fair, quote.price, promo=quote.promo)
            if fair is not None
            else None
        )
        edge = evaluation.ev_promo if evaluation is not None else None
        edge_model = (
            promo_ev(model_probability, quote.price, promo=quote.promo).ev_promo
            if model_probability is not None
            else None
        )
        verdicts.append(
            QuoteVerdict(
                bookmaker=quote.bookmaker,
                price_quoted=quote.price,
                promo=quote.promo,
                source=quote.source,
                evaluation=evaluation,
                edge=edge,
                edge_model=edge_model,
                verdict=_judge(
                    edge, anchored=anchor is not None, fresh=fresh,
                    min_edge=min_edge, source=quote.source,
                ),
            )
        )
    verdicts.sort(
        key=lambda v: v.edge if v.edge is not None else float("-inf"),
        reverse=True,
    )
    return ScanResult(
        event_id=resolved_id,
        home_team=home,
        away_team=away,
        commence_time=kickoff,
        market=market,
        line=anchor.line if anchor is not None else line,
        anchor=anchor,
        anchor_age=anchor_age,
        model_probability=model_probability,
        verdicts=verdicts,
    )
