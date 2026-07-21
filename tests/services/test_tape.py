"""Tape read-model tests — offline, in-memory SQLite.

The tape is append-only and unjudged (ADR 0012); this read-model is where
judgment happens: "the Pinnacle fair right now" must be the latest *complete*
selection set at or before the asked-for instant — never a later one (the
scanner and the ledger both anchor real-money decisions here, so lookahead
would be a lie about what was knowable).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from pitchprob.betting.odds_math import remove_overround_shin
from pitchprob.data.orm import Base, OddsTick
from pitchprob.services.tape import closing_fair, fair_at, find_events

_KICKOFF = datetime(2026, 8, 21, 19, 0, tzinfo=UTC)
_T1 = datetime(2026, 8, 19, 8, 0, tzinfo=UTC)
_T2 = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)


def _tick(
    *,
    selection: str,
    price: str,
    observed_at: datetime,
    market: str = "1x2",
    line: str | None = None,
    bookmaker: str = "pinnacle",
    event_id: str = "ev1",
    home: str = "Arsenal",
    away: str = "Coventry City",
    commence: datetime = _KICKOFF,
) -> OddsTick:
    return OddsTick(
        sport_key="soccer_epl",
        event_id=event_id,
        commence_time=commence,
        home_team=home,
        away_team=away,
        bookmaker=bookmaker,
        market=market,
        selection=selection,
        line=None if line is None else Decimal(line),
        price=Decimal(price),
        observed_at=observed_at,
    )


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _seed_1x2(session: Session) -> None:
    for when, prices in [
        (_T1, ("2.10", "3.60", "3.40")),
        (_T2, ("2.00", "3.70", "3.60")),
    ]:
        for selection, price in zip(("home", "draw", "away"), prices, strict=True):
            session.add(_tick(selection=selection, price=price, observed_at=when))
    session.flush()


class TestFairAt:
    def test_latest_snapshot_wins_and_shin_matches_canonical(
        self, session: Session
    ) -> None:
        _seed_1x2(session)
        quote = fair_at(session, event_id="ev1", market="1x2")
        assert quote is not None
        assert quote.observed_at.replace(tzinfo=UTC) == _T2
        assert quote.prices["home"] == Decimal("2.00")
        expected = remove_overround_shin([2.00, 3.70, 3.60])
        for selection, prob in zip(("home", "draw", "away"), expected, strict=True):
            assert quote.probabilities[selection] == pytest.approx(prob)
        assert sum(quote.probabilities.values()) == pytest.approx(1.0)

    def test_no_lookahead_past_the_asked_instant(self, session: Session) -> None:
        _seed_1x2(session)
        quote = fair_at(
            session, event_id="ev1", market="1x2", at=_T1 + timedelta(hours=2)
        )
        assert quote is not None
        assert quote.observed_at.replace(tzinfo=UTC) == _T1
        assert quote.prices["home"] == Decimal("2.10")

    def test_before_any_snapshot_returns_none(self, session: Session) -> None:
        _seed_1x2(session)
        assert (
            fair_at(
                session, event_id="ev1", market="1x2", at=_T1 - timedelta(days=1)
            )
            is None
        )

    def test_incomplete_latest_snapshot_falls_back_to_complete_one(
        self, session: Session
    ) -> None:
        _seed_1x2(session)
        # A later partial snapshot (draw price missing) must not be used.
        t3 = _T2 + timedelta(days=1)
        session.add(_tick(selection="home", price="1.95", observed_at=t3))
        session.add(_tick(selection="away", price="3.80", observed_at=t3))
        session.flush()
        quote = fair_at(session, event_id="ev1", market="1x2")
        assert quote is not None
        assert quote.observed_at.replace(tzinfo=UTC) == _T2

    def test_symmetric_totals_book_is_fair_fifty_fifty(
        self, session: Session
    ) -> None:
        for selection in ("over", "under"):
            session.add(
                _tick(
                    selection=selection, price="1.89", observed_at=_T1,
                    market="ou", line="3.0",
                )
            )
        session.flush()
        quote = fair_at(session, event_id="ev1", market="ou", line=Decimal("3.0"))
        assert quote is not None
        assert quote.line == Decimal("3.0")
        assert quote.probabilities["over"] == pytest.approx(0.5)
        assert quote.probabilities["under"] == pytest.approx(0.5)

    def test_line_mismatch_returns_none(self, session: Session) -> None:
        for selection in ("over", "under"):
            session.add(
                _tick(
                    selection=selection, price="1.89", observed_at=_T1,
                    market="ou", line="3.0",
                )
            )
        session.flush()
        assert (
            fair_at(session, event_id="ev1", market="ou", line=Decimal("2.5"))
            is None
        )

    def test_line_none_picks_the_most_balanced_line(self, session: Session) -> None:
        for line, over, under in [("2.5", "1.55", "2.45"), ("3.0", "1.89", "1.89")]:
            session.add(
                _tick(
                    selection="over", price=over, observed_at=_T1,
                    market="ou", line=line,
                )
            )
            session.add(
                _tick(
                    selection="under", price=under, observed_at=_T1,
                    market="ou", line=line,
                )
            )
        session.flush()
        quote = fair_at(session, event_id="ev1", market="ou")
        assert quote is not None
        assert quote.line == Decimal("3.0")

    def test_other_bookmakers_are_ignored(self, session: Session) -> None:
        _seed_1x2(session)
        session.add(
            _tick(
                selection="home", price="1.50", observed_at=_T2 + timedelta(days=1),
                bookmaker="betclic",
            )
        )
        session.flush()
        quote = fair_at(session, event_id="ev1", market="1x2")
        assert quote is not None
        assert quote.prices["home"] == Decimal("2.00")


class TestClosingFair:
    def test_last_snapshot_before_kickoff(self, session: Session) -> None:
        _seed_1x2(session)
        # A tick after kickoff (in-play or next-event pollution) must lose to
        # the last pre-match snapshot.
        for selection, price in zip(
            ("home", "draw", "away"), ("1.10", "9.0", "20.0"), strict=True
        ):
            session.add(
                _tick(
                    selection=selection, price=price,
                    observed_at=_KICKOFF + timedelta(minutes=30),
                )
            )
        session.flush()
        quote = closing_fair(session, event_id="ev1", market="1x2")
        assert quote is not None
        assert quote.observed_at.replace(tzinfo=UTC) == _T2
        assert quote.commence_time.replace(tzinfo=UTC) == _KICKOFF

    def test_no_pre_kickoff_snapshot_returns_none(self, session: Session) -> None:
        for selection, price in zip(
            ("home", "draw", "away"), ("1.10", "9.0", "20.0"), strict=True
        ):
            session.add(
                _tick(
                    selection=selection, price=price,
                    observed_at=_KICKOFF + timedelta(minutes=5),
                )
            )
        session.flush()
        assert closing_fair(session, event_id="ev1", market="1x2") is None


class TestCornersMarkets:
    """Corners (ADR 0014) reuse the goals shapes, so the read-model needs no
    new code path — these pin that the reuse actually holds, including the
    invariant that matters most: no lookahead."""

    def test_corners_totals_de_margin_like_goals_totals(
        self, session: Session
    ) -> None:
        for selection, price in (("over", "1.75"), ("under", "1.85")):
            session.add(
                _tick(
                    selection=selection, price=price, observed_at=_T1,
                    market="corners_ou", line="10.5",
                )
            )
        session.flush()
        quote = fair_at(
            session, event_id="ev1", market="corners_ou",
            at=_T1 + timedelta(hours=1),
        )
        assert quote is not None
        assert quote.line == Decimal("10.5")
        expected = remove_overround_shin([1.75, 1.85])
        assert quote.probabilities["over"] == pytest.approx(expected[0])
        assert quote.probabilities["under"] == pytest.approx(expected[1])

    def test_corners_handicap_selections_are_home_and_away(
        self, session: Session
    ) -> None:
        for selection, price in (("home", "1.80"), ("away", "1.95")):
            session.add(
                _tick(
                    selection=selection, price=price, observed_at=_T1,
                    market="corners_ah", line="-0.5",
                )
            )
        session.flush()
        quote = fair_at(
            session, event_id="ev1", market="corners_ah", at=_T2
        )
        assert quote is not None
        assert set(quote.probabilities) == {"home", "away"}
        assert quote.line == Decimal("-0.5")

    def test_a_later_corners_snapshot_is_never_used(
        self, session: Session
    ) -> None:
        for observed, over, under in (
            (_T1, "1.75", "1.85"),
            (_T2, "1.50", "2.30"),
        ):
            for selection, price in (("over", over), ("under", under)):
                session.add(
                    _tick(
                        selection=selection, price=price, observed_at=observed,
                        market="corners_ou", line="10.5",
                    )
                )
        session.flush()
        quote = fair_at(
            session, event_id="ev1", market="corners_ou",
            at=_T2 - timedelta(minutes=1),
        )
        assert quote is not None
        assert quote.observed_at.replace(tzinfo=UTC) == _T1
        assert quote.prices["over"] == Decimal("1.75")


class TestFindEvents:
    def test_matches_substring_case_insensitively(self, session: Session) -> None:
        _seed_1x2(session)
        session.add(
            _tick(
                selection="home", price="2.5", observed_at=_T1,
                event_id="ev2", home="Everton", away="Leeds United",
                commence=_KICKOFF + timedelta(days=1),
            )
        )
        session.flush()
        now = _T2
        events = find_events(session, "arsenal", at=now)
        assert [e.event_id for e in events] == ["ev1"]
        assert events[0].home_team == "Arsenal"
        # away-team match works too
        assert [e.event_id for e in find_events(session, "leeds", at=now)] == ["ev2"]

    def test_past_events_are_excluded(self, session: Session) -> None:
        _seed_1x2(session)
        assert find_events(session, "arsenal", at=_KICKOFF + timedelta(hours=3)) == []
