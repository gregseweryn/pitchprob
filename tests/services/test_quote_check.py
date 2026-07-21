"""Feed-validation tests — offline, in-memory SQLite.

The design claim being pinned: a feed fails validation for three separate
reasons (wrong, missing, stale) and none of them may be quietly folded into
the others.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from pitchprob.data.adapters.odds_api_io import SOURCE
from pitchprob.data.orm import Base, OddsTick
from pitchprob.services.quote_check import (
    MIN_CHECKS,
    log_quote_check,
    quote_check_report,
)

_KICKOFF = datetime(2026, 8, 21, 19, 0, tzinfo=UTC)
_NOW = _KICKOFF - timedelta(hours=5)


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _feed_tick(
    session: Session,
    *,
    price: str,
    observed_at: datetime,
    bookmaker: str = "Betclic PL",
    source: str = SOURCE,
) -> None:
    session.add(
        OddsTick(
            source=source,
            sport_key="england-premier-league",
            event_id="ev1",
            commence_time=_KICKOFF,
            home_team="Arsenal",
            away_team="Coventry City",
            bookmaker=bookmaker,
            market="1x2",
            selection="home",
            line=None,
            price=Decimal(price),
            observed_at=observed_at,
        )
    )
    session.flush()


def _log(session: Session, *, seen: str, at: datetime = _NOW):
    return log_quote_check(
        session,
        bookmaker="Betclic PL",
        home_team="Arsenal",
        away_team="Coventry City",
        market="1x2",
        selection="home",
        price_seen=Decimal(seen),
        event_id="ev1",
        checked_at=at,
    )


class TestLogging:
    def test_the_feed_price_at_that_instant_is_attached(
        self, session: Session
    ) -> None:
        _feed_tick(session, price="1.85", observed_at=_NOW - timedelta(minutes=5))
        check = _log(session, seen="1.85")
        assert check.price_feed == Decimal("1.85")
        assert check.feed_observed_at is not None

    def test_a_later_feed_tick_is_never_used(self, session: Session) -> None:
        """Reading forward would credit the feed with a price it did not
        show when the operator looked."""
        _feed_tick(session, price="1.85", observed_at=_NOW - timedelta(minutes=5))
        _feed_tick(session, price="1.70", observed_at=_NOW + timedelta(minutes=5))
        check = _log(session, seen="1.85")
        assert check.price_feed == Decimal("1.85")

    def test_no_feed_coverage_records_a_null_not_a_guess(
        self, session: Session
    ) -> None:
        check = _log(session, seen="1.85")
        assert check.price_feed is None
        assert check.feed_observed_at is None

    def test_ticks_from_the_main_tape_are_not_this_feed(
        self, session: Session
    ) -> None:
        """The main tape has `betclic_fr`, a different book on a different
        source; it must never stand in for the PL feed."""
        _feed_tick(
            session, price="1.85", observed_at=_NOW - timedelta(minutes=5),
            source="the-odds-api",
        )
        assert _log(session, seen="1.85").price_feed is None

    def test_a_bad_market_shape_is_rejected_before_storing(
        self, session: Session
    ) -> None:
        with pytest.raises(ValueError):
            log_quote_check(
                session,
                bookmaker="Betclic PL",
                home_team="Arsenal",
                away_team="Coventry City",
                market="1x2",
                selection="over",  # not a 1X2 selection
                price_seen=Decimal("1.85"),
            )


class TestReport:
    def test_the_three_failure_modes_are_counted_separately(
        self, session: Session
    ) -> None:
        recent = _NOW - timedelta(minutes=5)
        _feed_tick(session, price="1.85", observed_at=recent)
        _log(session, seen="1.85")                     # agreed
        _log(session, seen="2.05", at=_NOW + timedelta(seconds=1))  # disagreed
        _feed_tick(session, price="1.85", observed_at=_NOW - timedelta(hours=3))
        _log(session, seen="1.85", at=_NOW - timedelta(hours=2))    # stale
        # a selection the feed never carried
        log_quote_check(
            session,
            bookmaker="Betclic PL",
            home_team="Arsenal",
            away_team="Coventry City",
            market="ou",
            selection="over",
            line=Decimal("2.5"),
            price_seen=Decimal("1.90"),
            event_id="ev1",
            checked_at=_NOW,
        )
        verdicts, report = quote_check_report(session)
        v = verdicts[0]
        assert (v.agreed, v.disagreed, v.stale, v.missing) == (1, 1, 1, 1)
        assert v.agreement_rate == pytest.approx(0.5)
        assert v.missing_rate == pytest.approx(0.5)
        assert v.max_abs_delta == Decimal("0.200")
        assert "not yet" in report

    def test_the_evidence_bar_is_not_cleared_by_a_perfect_small_sample(
        self, session: Session
    ) -> None:
        """Ten flawless checks are not two weeks of validation."""
        for index in range(10):
            at = _NOW + timedelta(minutes=index)
            _feed_tick(session, price="1.85", observed_at=at - timedelta(minutes=1))
            _log(session, seen="1.85", at=at)
        verdicts, _ = quote_check_report(session)
        assert verdicts[0].agreement_rate == pytest.approx(1.0)
        assert verdicts[0].checks < MIN_CHECKS
        assert verdicts[0].passes is False

    def test_a_clean_run_at_the_bar_passes(self, session: Session) -> None:
        for index in range(MIN_CHECKS):
            at = _NOW + timedelta(minutes=index)
            _feed_tick(session, price="1.85", observed_at=at - timedelta(minutes=1))
            _log(session, seen="1.85", at=at)
        verdicts, _ = quote_check_report(session)
        assert verdicts[0].passes is True

    def test_an_empty_table_says_manual_entry_is_still_ground_truth(
        self, session: Session
    ) -> None:
        verdicts, report = quote_check_report(session)
        assert verdicts == []
        assert "ground truth" in report
