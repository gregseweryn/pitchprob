"""Validating the odds-api.io feed against what the operator actually sees.

The audit's condition for letting an automatic PL-quote feed near the
scanner is two to three weeks of hand validation first, with manual entry
staying ground truth throughout (Etap 5). This module is that instrument.

The verdict thresholds below are set **now, before any data exists**, for
the same reason the experiment registry pins its hypotheses up front: a bar
chosen after seeing the numbers is not a bar. A feed passes only if it
agrees closely and is rarely missing — those are separate failure modes and
a feed that is right whenever it speaks but silent a third of the time is
still not a scanner input.

Note what is *not* measured here: whether the price was takeable. A feed and
a screenshot can agree perfectly on a price the book would have refused.
That question belongs to the pick ledger, where executed prices are
recorded.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pitchprob.data.adapters.odds_api_io import SOURCE
from pitchprob.data.orm import OddsTick, QuoteCheck
from pitchprob.services.tape import as_utc, validate_market_selection

#: Absolute decimal-odds difference within which feed and screen agree.
#: 0.02 is one tick on most PL boards — smaller would fail on rounding,
#: larger would wave through a difference big enough to flip a verdict.
AGREEMENT_TOLERANCE = Decimal("0.02")

#: A feed quote older than this is not evidence about the price on screen.
MAX_FEED_AGE = timedelta(minutes=30)

#: The promotion bar, fixed in advance (ADR 0014).
MIN_CHECKS = 30
MIN_AGREEMENT = 0.95
MAX_MISSING = 0.10


@dataclass(frozen=True, slots=True)
class BookVerdict:
    bookmaker: str
    checks: int
    missing: int
    agreed: int
    disagreed: int
    stale: int
    max_abs_delta: Decimal | None
    agreement_rate: float | None
    missing_rate: float
    passes: bool


def _feed_price(
    session: Session,
    *,
    bookmaker: str,
    event_id: str | None,
    market: str,
    selection: str,
    line: Decimal | None,
    at: datetime,
) -> tuple[Decimal, datetime] | None:
    """The feed's latest quote for this selection at or before ``at``."""
    if event_id is None:
        return None
    query = (
        select(OddsTick.price, OddsTick.observed_at)
        .where(
            OddsTick.source == SOURCE,
            OddsTick.bookmaker == bookmaker,
            OddsTick.event_id == event_id,
            OddsTick.market == market,
            OddsTick.selection == selection,
        )
        .order_by(OddsTick.observed_at.desc())
    )
    if line is None:
        query = query.where(OddsTick.line.is_(None))
    else:
        query = query.where(OddsTick.line == line)
    for price, observed_at in session.execute(query).all():
        when = as_utc(observed_at)
        if when <= at:
            return price, when
    return None


def log_quote_check(
    session: Session,
    *,
    bookmaker: str,
    home_team: str,
    away_team: str,
    market: str,
    selection: str,
    price_seen: Decimal,
    line: Decimal | None = None,
    event_id: str | None = None,
    checked_at: datetime | None = None,
    notes: str | None = None,
) -> QuoteCheck:
    """Record one sighting and attach whatever the feed said at that moment.

    The feed lookup is bounded by ``checked_at``: reading a later feed tick
    would flatter the feed with information it did not have when the
    operator looked.
    """
    validate_market_selection(market, selection, line)
    when = checked_at if checked_at is not None else datetime.now(tz=UTC)
    found = _feed_price(
        session,
        bookmaker=bookmaker,
        event_id=event_id,
        market=market,
        selection=selection,
        line=line,
        at=when,
    )
    check = QuoteCheck(
        checked_at=when,
        bookmaker=bookmaker,
        event_id=event_id,
        home_team=home_team,
        away_team=away_team,
        market=market,
        selection=selection,
        line=line,
        price_seen=price_seen,
        price_feed=found[0] if found else None,
        feed_observed_at=found[1] if found else None,
        notes=notes,
    )
    session.add(check)
    session.flush()
    return check


def _classify(
    check: QuoteCheck, *, tolerance: Decimal, max_age: timedelta
) -> str:
    if check.price_feed is None or check.feed_observed_at is None:
        return "missing"
    if as_utc(check.checked_at) - as_utc(check.feed_observed_at) > max_age:
        return "stale"
    if abs(check.price_feed - check.price_seen) <= tolerance:
        return "agreed"
    return "disagreed"


def quote_check_report(
    session: Session,
    *,
    tolerance: Decimal = AGREEMENT_TOLERANCE,
    max_age: timedelta = MAX_FEED_AGE,
) -> tuple[list[BookVerdict], str]:
    """Per-book verdicts plus a rendered report."""
    checks = list(session.execute(select(QuoteCheck)).scalars().all())
    by_book: dict[str, list[QuoteCheck]] = {}
    for check in checks:
        by_book.setdefault(check.bookmaker, []).append(check)

    verdicts: list[BookVerdict] = []
    for bookmaker, items in sorted(by_book.items()):
        kinds = [
            _classify(c, tolerance=tolerance, max_age=max_age) for c in items
        ]
        agreed = kinds.count("agreed")
        disagreed = kinds.count("disagreed")
        missing = kinds.count("missing")
        stale = kinds.count("stale")
        comparable = agreed + disagreed
        deltas = [
            abs(c.price_feed - c.price_seen)
            for c, kind in zip(items, kinds, strict=True)
            if kind in ("agreed", "disagreed") and c.price_feed is not None
        ]
        agreement = agreed / comparable if comparable else None
        missing_rate = (missing + stale) / len(items)
        verdicts.append(
            BookVerdict(
                bookmaker=bookmaker,
                checks=len(items),
                missing=missing,
                agreed=agreed,
                disagreed=disagreed,
                stale=stale,
                max_abs_delta=max(deltas) if deltas else None,
                agreement_rate=agreement,
                missing_rate=missing_rate,
                passes=(
                    len(items) >= MIN_CHECKS
                    and agreement is not None
                    and agreement >= MIN_AGREEMENT
                    and missing_rate <= MAX_MISSING
                ),
            )
        )
    return verdicts, _render(verdicts)


def _render(verdicts: list[BookVerdict]) -> str:
    lines = [
        "# Feed validation — odds-api.io vs the operator's screen",
        "",
        f"Bar set in advance: >= {MIN_CHECKS} checks, "
        f"agreement >= {MIN_AGREEMENT:.0%}, missing/stale <= {MAX_MISSING:.0%}.",
        "",
    ]
    if not verdicts:
        lines += [
            "No checks logged yet. Until this table has entries, the feed is",
            "informational and manual entry is the only ground truth.",
        ]
        return "\n".join(lines)
    lines += [
        "| book | checks | agreed | disagreed | missing | stale | "
        "agreement | worst delta | verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for v in verdicts:
        rate = "—" if v.agreement_rate is None else f"{v.agreement_rate:.0%}"
        worst = "—" if v.max_abs_delta is None else f"{v.max_abs_delta}"
        lines.append(
            f"| {v.bookmaker} | {v.checks} | {v.agreed} | {v.disagreed} "
            f"| {v.missing} | {v.stale} | {rate} | {worst} "
            f"| {'PASS' if v.passes else 'not yet'} |"
        )
    lines += [
        "",
        "A book reads 'not yet' until it clears every part of the bar. Missing",
        "and stale count against the feed as much as wrong prices do: a quote",
        "the scanner cannot get in time is a quote it does not have.",
    ]
    return "\n".join(lines)


def summary_metrics(verdicts: list[BookVerdict]) -> dict[str, Any]:
    return {
        "study": "quote-check",
        "books": [
            {
                "bookmaker": v.bookmaker,
                "checks": v.checks,
                "agreement_rate": v.agreement_rate,
                "missing_rate": v.missing_rate,
                "passes": v.passes,
            }
            for v in verdicts
        ],
    }
