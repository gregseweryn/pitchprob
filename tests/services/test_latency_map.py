"""Latency-map service tests — offline, in-memory SQLite tape.

The load-bearing case is the first one: on today's real tape this report
must say "nothing measurable here, and here is why" rather than produce a
number. Everything else pins that a synthetic tape with a real lag in it
recovers that lag.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from pitchprob.data.orm import Base, OddsTick
from pitchprob.services.latency_map import run_latency_map

_KICKOFF = datetime(2026, 8, 21, 19, 0, tzinfo=UTC)
_T0 = _KICKOFF - timedelta(days=2)


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _book(
    session: Session,
    *,
    bookmaker: str,
    hours: float,
    home: str,
    draw: str,
    away: str,
    source: str = "odds-api-io",
    event_id: str = "ev1",
) -> None:
    """One complete 1X2 quote from one book at one instant."""
    for selection, price in (("home", home), ("draw", draw), ("away", away)):
        session.add(
            OddsTick(
                source=source,
                sport_key="soccer_epl",
                event_id=event_id,
                commence_time=_KICKOFF,
                home_team="Arsenal",
                away_team="Coventry City",
                bookmaker=bookmaker,
                market="1x2",
                selection=selection,
                line=None,
                price=Decimal(price),
                observed_at=_T0 + timedelta(hours=hours),
            )
        )


class TestEmptyAndDegenerateTapes:
    def test_an_empty_tape_reports_no_data_instead_of_failing(
        self, session: Session
    ) -> None:
        result = run_latency_map(session)
        assert result.metrics["reference_moves"] == 0
        assert result.metrics["sufficient"] is False
        assert "no ticks" in result.report

    def test_a_single_snapshot_names_the_resolution_floor(
        self, session: Session
    ) -> None:
        """This is today's real tape: one snapshot, so no series, so no
        latency — and the report has to say that, not imply speed."""
        _book(session, bookmaker="pinnacle", hours=0,
              home="1.50", draw="4.00", away="6.00", source="the-odds-api")
        _book(session, bookmaker="bet365", hours=0,
              home="1.48", draw="3.90", away="5.80", source="the-odds-api")
        session.flush()
        result = run_latency_map(session, source="the-odds-api")
        assert result.metrics["reference_moves"] == 0
        assert result.metrics["sufficient"] is False
        assert result.metrics["resolution_floor_hours"] is None
        assert "single snapshot" in result.report

    def test_a_tape_without_the_reference_book_says_so(
        self, session: Session
    ) -> None:
        _book(session, bookmaker="betclic_pl", hours=0,
              home="1.50", draw="4.00", away="6.00")
        _book(session, bookmaker="betclic_pl", hours=6,
              home="1.40", draw="4.20", away="6.50")
        session.flush()
        result = run_latency_map(session, reference="pinnacle")
        assert result.metrics["reference_moves"] == 0
        assert "pinnacle" in result.report


class TestRecoveringAKnownLag:
    def _seed(self, session: Session) -> None:
        # Reference moves at +6h: home 1.50 -> 1.30 (a large, real move).
        _book(session, bookmaker="pinnacle", hours=0,
              home="1.50", draw="4.00", away="6.00")
        _book(session, bookmaker="pinnacle", hours=6,
              home="1.30", draw="4.80", away="9.00")
        _book(session, bookmaker="pinnacle", hours=12,
              home="1.30", draw="4.80", away="9.00")
        # A fast follower copies it by +8h; a slow one only by +12h.
        for hours, prices in (
            (0, ("1.48", "3.90", "5.80")),
            (8, ("1.28", "4.70", "8.70")),
            (12, ("1.28", "4.70", "8.70")),
        ):
            _book(session, bookmaker="betclic_pl", hours=hours,
                  home=prices[0], draw=prices[1], away=prices[2])
        for hours, prices in (
            (0, ("1.48", "3.90", "5.80")),
            (8, ("1.47", "3.92", "5.85")),
            (12, ("1.28", "4.70", "8.70")),
        ):
            _book(session, bookmaker="sts_pl", hours=hours,
                  home=prices[0], draw=prices[1], away=prices[2])
        session.flush()

    def test_the_slower_book_shows_the_longer_delay(
        self, session: Session
    ) -> None:
        self._seed(session)
        result = run_latency_map(session, min_move=0.02, threshold=0.02)
        assert result.metrics["reference_moves"] == 1
        by_book = {row["bookmaker"]: row for row in result.metrics["books"]}
        assert by_book["betclic_pl"]["median_latency_hours"] == pytest.approx(2.0)
        assert by_book["sts_pl"]["median_latency_hours"] == pytest.approx(6.0)
        assert "pinnacle" not in by_book  # the reference never follows itself

    def test_the_report_leads_with_the_resolution_floor(
        self, session: Session
    ) -> None:
        self._seed(session)
        result = run_latency_map(session)
        assert result.metrics["resolution_floor_hours"] == pytest.approx(6.0)
        assert "6.0h" in result.report
        # One reference move is not a finding, and the report must say so
        # before anyone quotes the medians above.
        assert result.metrics["sufficient"] is False
        assert "not a finding" in result.report

    def test_reaching_the_evidence_bar_flips_the_verdict(
        self, session: Session
    ) -> None:
        self._seed(session)
        result = run_latency_map(session, min_observed=1)
        assert result.metrics["sufficient"] is True
