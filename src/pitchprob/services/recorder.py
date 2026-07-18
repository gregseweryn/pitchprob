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

import csv
import gzip
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pitchprob.data.adapters.odds_api import SPORT_KEYS, OddsSnapshot, OddsTickRecord
from pitchprob.data.orm import OddsTick


class _OddsClient(Protocol):
    def fetch_odds(self, sport_key: str, *, markets: list[str]) -> OddsSnapshot: ...


@dataclass(frozen=True, slots=True)
class RecorderSummary:
    ticks_inserted: int
    events_seen: int
    bookmakers: list[str]
    requests_remaining: int | None


_CSV_COLUMNS = [
    "sport_key", "event_id", "commence_time", "home_team", "away_team",
    "bookmaker", "market", "selection", "line", "price", "observed_at",
]


def _validate_leagues(leagues: list[str]) -> None:
    unknown = [league for league in leagues if league not in SPORT_KEYS]
    if unknown:
        known = ", ".join(sorted(SPORT_KEYS))
        raise ValueError(f"unknown league(s) {unknown!r} (known: {known})")


def _fetch_all(
    client: _OddsClient, leagues: list[str], markets: list[str]
) -> tuple[list[OddsTickRecord], int | None]:
    ticks: list[OddsTickRecord] = []
    remaining: int | None = None
    for league in leagues:
        snapshot = client.fetch_odds(SPORT_KEYS[league], markets=markets)
        if snapshot.requests_remaining is not None:
            remaining = snapshot.requests_remaining
        ticks.extend(snapshot.ticks)
    return ticks, remaining


def _summary(
    ticks: list[OddsTickRecord], remaining: int | None
) -> RecorderSummary:
    return RecorderSummary(
        ticks_inserted=len(ticks),
        events_seen=len({f"{t.sport_key}:{t.event_id}" for t in ticks}),
        bookmakers=sorted({t.bookmaker for t in ticks}),
        requests_remaining=remaining,
    )


def record_snapshot(
    session: Session,
    client: _OddsClient,
    *,
    leagues: list[str],
    markets: list[str],
    observed_at: datetime | None = None,
) -> RecorderSummary:
    _validate_leagues(leagues)
    when = observed_at if observed_at is not None else datetime.now(tz=UTC)
    ticks, remaining = _fetch_all(client, leagues, markets)
    for tick in ticks:
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
    return _summary(ticks, remaining)


def record_snapshot_to_csv(
    client: _OddsClient,
    *,
    leagues: list[str],
    markets: list[str],
    directory: Path,
    observed_at: datetime | None = None,
) -> tuple[RecorderSummary, Path | None]:
    """The cloud-recorder sink (GitHub Actions, ADR 0012): one gzipped CSV
    per snapshot, committed to the repository — no database in the runner.
    An empty snapshot writes no file. ``import_tape`` merges the files into
    ``odds_ticks`` locally, idempotently."""
    _validate_leagues(leagues)
    when = observed_at if observed_at is not None else datetime.now(tz=UTC)
    ticks, remaining = _fetch_all(client, leagues, markets)
    if not ticks:
        return _summary(ticks, remaining), None
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{when:%Y%m%dT%H%M%S}Z.csv.gz"
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_CSV_COLUMNS)
    writer.writeheader()
    for tick in ticks:
        writer.writerow(
            {
                "sport_key": tick.sport_key,
                "event_id": tick.event_id,
                "commence_time": tick.commence_time.isoformat(),
                "home_team": tick.home_team,
                "away_team": tick.away_team,
                "bookmaker": tick.bookmaker,
                "market": tick.market,
                "selection": tick.selection,
                "line": "" if tick.line is None else str(tick.line),
                "price": repr(tick.price),
                "observed_at": when.isoformat(),
            }
        )
    path.write_bytes(gzip.compress(buffer.getvalue().encode("utf-8")))
    return _summary(ticks, remaining), path


def import_tape(session: Session, directory: Path) -> tuple[int, int]:
    """Merge cloud-recorded CSV snapshots into ``odds_ticks``.

    Idempotent per snapshot: a file whose ``observed_at`` already exists in
    the table is skipped whole — snapshots are atomic units of the tape.
    Returns (files imported, ticks inserted)."""
    files_imported = 0
    ticks_inserted = 0
    for path in sorted(directory.glob("*.csv.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            continue
        when = datetime.fromisoformat(rows[0]["observed_at"])
        existing = session.execute(
            select(func.count())
            .select_from(OddsTick)
            .where(OddsTick.observed_at == when)
        ).scalar_one()
        if existing:
            continue
        for row in rows:
            session.add(
                OddsTick(
                    sport_key=row["sport_key"],
                    event_id=row["event_id"],
                    commence_time=datetime.fromisoformat(row["commence_time"]),
                    home_team=row["home_team"],
                    away_team=row["away_team"],
                    bookmaker=row["bookmaker"],
                    market=row["market"],
                    selection=row["selection"],
                    line=Decimal(row["line"]) if row["line"] else None,
                    price=Decimal(row["price"]),
                    observed_at=datetime.fromisoformat(row["observed_at"]),
                )
            )
            ticks_inserted += 1
        files_imported += 1
    return files_imported, ticks_inserted
