"""Live odds snapshot recorder (Phase 5, ADR 0012).

Designed to be run on a schedule (cron / Task Scheduler): each invocation
takes one snapshot of the current pre-match books for the requested leagues
and appends every quote to the ``odds_ticks`` tape with a single
``observed_at`` timestamp. The tape is append-only and unjudged — analysis
decides later what a "tick" meant; the recorder's only job is to never miss
a day once the season starts.

The summary surfaces the two operational facts that matter: which bookmaker
keys actually appear (the empirical answer to "can we see Polish books?")
and how much API quota remains.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy.orm import Session

from pitchprob.data.adapters.odds_api import SPORT_KEYS, OddsSnapshot
from pitchprob.data.orm import OddsTick


class _OddsClient(Protocol):
    def fetch_odds(self, sport_key: str, *, markets: list[str]) -> OddsSnapshot: ...


@dataclass(frozen=True, slots=True)
class RecorderSummary:
    ticks_inserted: int
    events_seen: int
    bookmakers: list[str]
    requests_remaining: int | None


def record_snapshot(
    session: Session,
    client: _OddsClient,
    *,
    leagues: list[str],
    markets: list[str],
    observed_at: datetime | None = None,
) -> RecorderSummary:
    unknown = [league for league in leagues if league not in SPORT_KEYS]
    if unknown:
        known = ", ".join(sorted(SPORT_KEYS))
        raise ValueError(f"unknown league(s) {unknown!r} (known: {known})")
    when = observed_at if observed_at is not None else datetime.now(tz=UTC)

    inserted = 0
    events: set[str] = set()
    bookmakers: set[str] = set()
    remaining: int | None = None
    for league in leagues:
        snapshot = client.fetch_odds(SPORT_KEYS[league], markets=markets)
        if snapshot.requests_remaining is not None:
            remaining = snapshot.requests_remaining
        for tick in snapshot.ticks:
            events.add(f"{tick.sport_key}:{tick.event_id}")
            bookmakers.add(tick.bookmaker)
            session.add(
                OddsTick(
                    sport_key=tick.sport_key,
                    event_id=tick.event_id,
                    commence_time=tick.commence_time,
                    home_team=tick.home_team,
                    away_team=tick.away_team,
                    bookmaker=tick.bookmaker,
                    market=tick.market,
                    selection=tick.selection,
                    line=tick.line,
                    price=Decimal(str(tick.price)),
                    observed_at=when,
                )
            )
            inserted += 1
    return RecorderSummary(
        ticks_inserted=inserted,
        events_seen=len(events),
        bookmakers=sorted(bookmakers),
        requests_remaining=remaining,
    )
