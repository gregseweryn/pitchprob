"""The risk layer as the ledger enforces it (Phase 3, ADR 0015).

The primitives are unit-tested in tests/betting/test_risk.py; these cover
the part that touches money: what the ledger reads out of the database to
build the exposure state, and that a refusal is a refusal — an override
leaves a permanent mark on the pick, because an override nobody can see
afterwards is worse than having no breaker.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from pitchprob.betting.risk import DEFAULT_LIMITS, RiskLimits, RiskRefusal
from pitchprob.data.orm import Base, Pick
from pitchprob.services.ledger import (
    exposure_state,
    log_pick,
    realized_drawdown,
    settle_pick,
)

_KICKOFF = datetime(2026, 8, 21, 19, 0, tzinfo=UTC)
_PLACED = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _log(session: Session, **overrides: object) -> Pick:
    params: dict[str, object] = {
        "home_team": "Arsenal",
        "away_team": "Coventry City",
        "kickoff_utc": _KICKOFF,
        "market": "ou",
        "selection": "over",
        "line": Decimal("3.0"),
        "bookmaker": "betclic",
        "stake_pln": Decimal("5"),
        "price_quoted": Decimal("2.10"),
        "placed_at": _PLACED,
        "event_id": "ev1",
    }
    params.update(overrides)
    return log_pick(session, **params)  # type: ignore[arg-type]


class TestExposureState:
    def test_same_fixture_stake_is_summed_across_markets_and_books(
        self, session: Session
    ) -> None:
        _log(session, stake_pln=Decimal("3"))
        _log(
            session, stake_pln=Decimal("2"), market="1x2", selection="home",
            line=None, bookmaker="sts",
        )
        state = exposure_state(
            session, event_id="ev1", home_team="Arsenal",
            away_team="Coventry City", kickoff_utc=_KICKOFF, placed_at=_PLACED,
        )
        assert state.match_stake_pln == Decimal("5")

    def test_a_different_fixture_does_not_count_toward_the_match_cap(
        self, session: Session
    ) -> None:
        _log(session, stake_pln=Decimal("5"))
        state = exposure_state(
            session, event_id="ev2", home_team="Everton", away_team="Fulham",
            kickoff_utc=_KICKOFF, placed_at=_PLACED,
        )
        assert state.match_stake_pln == Decimal("0")
        assert state.daily_stake_pln == Decimal("5")  # but the day still counts

    def test_fixtures_are_matched_by_teams_when_no_event_id_exists(
        self, session: Session
    ) -> None:
        """Manually logged picks carry no tape event id; the per-match cap
        must still bind, or the correlation control has a hole in it."""
        _log(session, event_id=None, stake_pln=Decimal("5"))
        state = exposure_state(
            session, event_id=None, home_team="Arsenal",
            away_team="Coventry City", kickoff_utc=_KICKOFF, placed_at=_PLACED,
        )
        assert state.match_stake_pln == Decimal("5")

    def test_the_daily_window_is_the_utc_calendar_day_of_placement(
        self, session: Session
    ) -> None:
        _log(session, stake_pln=Decimal("5"), placed_at=_PLACED)
        # 23:59 the same UTC day still counts; 00:01 the next day does not
        same_day = exposure_state(
            session, event_id="ev2", home_team="Everton", away_team="Fulham",
            kickoff_utc=_KICKOFF,
            placed_at=_PLACED.replace(hour=23, minute=59),
        )
        assert same_day.daily_stake_pln == Decimal("5")
        next_day = exposure_state(
            session, event_id="ev2", home_team="Everton", away_team="Fulham",
            kickoff_utc=_KICKOFF, placed_at=_PLACED + timedelta(days=1),
        )
        assert next_day.daily_stake_pln == Decimal("0")

    def test_open_picks_count_only_the_unsettled(self, session: Session) -> None:
        first = _log(session, stake_pln=Decimal("5"))
        _log(
            session, stake_pln=Decimal("5"), event_id="ev2",
            home_team="Everton", away_team="Fulham",
        )
        settle_pick(first, ft_home=2, ft_away=2, settled_at=_KICKOFF)
        session.flush()
        state = exposure_state(
            session, event_id="ev3", home_team="Leeds United",
            away_team="Burnley", kickoff_utc=_KICKOFF, placed_at=_PLACED,
        )
        assert state.open_picks == 1


class TestLimitEnforcement:
    def test_a_second_bet_on_the_same_fixture_is_refused(
        self, session: Session
    ) -> None:
        _log(session, stake_pln=Decimal("5"))
        with pytest.raises(RiskRefusal, match="max_match_stake"):
            _log(session, stake_pln=Decimal("2"), market="1x2",
                 selection="home", line=None)

    def test_the_refused_bet_is_not_written_to_the_ledger(
        self, session: Session
    ) -> None:
        """A refusal that still leaves a row would corrupt the very sample
        the limits exist to protect."""
        _log(session, stake_pln=Decimal("5"))
        with pytest.raises(RiskRefusal):
            _log(session, stake_pln=Decimal("5"), market="1x2",
                 selection="home", line=None)
        session.flush()
        assert session.query(Pick).count() == 1

    def test_the_sixth_bet_of_the_day_is_refused(self, session: Session) -> None:
        for index in range(5):
            _log(
                session, stake_pln=Decimal("5"), event_id=f"ev{index}",
                home_team=f"Home {index}", away_team=f"Away {index}",
            )
        with pytest.raises(RiskRefusal, match="max_daily_stake"):
            _log(
                session, stake_pln=Decimal("5"), event_id="ev9",
                home_team="Leeds United", away_team="Burnley",
            )

    def test_an_override_is_allowed_and_permanently_recorded(
        self, session: Session
    ) -> None:
        _log(session, stake_pln=Decimal("5"))
        pick = _log(
            session, stake_pln=Decimal("2"), market="1x2", selection="home",
            line=None, override_risk=True,
        )
        assert pick.risk_override is True
        assert pick.risk_note is not None
        assert "max_match_stake" in pick.risk_note

    def test_a_clean_pick_is_not_marked_as_an_override(
        self, session: Session
    ) -> None:
        pick = _log(session, stake_pln=Decimal("5"), override_risk=True)
        assert pick.risk_override is False
        assert pick.risk_note is None


class TestDrawdownBreaker:
    def _lose(self, session: Session, count: int, *, settled_at: datetime) -> None:
        """`count` settled 5 PLN losers — each is -5.00 realized."""
        for index in range(count):
            pick = log_pick(
                session,
                home_team=f"Home {index}", away_team=f"Away {index}",
                kickoff_utc=_KICKOFF, market="1x2", selection="home",
                bookmaker="sts", stake_pln=Decimal("5"),
                price_quoted=Decimal("2.10"),
                placed_at=_PLACED - timedelta(days=index + 1),
                event_id=f"old{index}",
            )
            # away win: the home bet loses outright
            settle_pick(pick, ft_home=0, ft_away=1, settled_at=settled_at)
        session.flush()

    def test_realized_drawdown_counts_settled_picks_only(
        self, session: Session
    ) -> None:
        self._lose(session, 3, settled_at=_PLACED - timedelta(hours=1))
        _log(session, stake_pln=Decimal("5"))  # open, unsettled
        state = realized_drawdown(session, bankroll=Decimal("500"))
        assert state.equity_pln == Decimal("485.00")
        assert state.drawdown_pln == Decimal("15.00")

    def test_below_the_threshold_betting_continues(self, session: Session) -> None:
        self._lose(session, 14, settled_at=_PLACED - timedelta(hours=1))
        assert realized_drawdown(session).drawdown_pln == Decimal("70.00")
        _log(session, stake_pln=Decimal("5"))  # must not raise

    def test_at_the_threshold_the_ledger_refuses_the_next_bet(
        self, session: Session
    ) -> None:
        # 15 losers x 5 PLN = 75.00 = exactly 15% of a 500 PLN bankroll
        self._lose(session, 15, settled_at=_PLACED - timedelta(hours=1))
        assert realized_drawdown(session).drawdown_pln == Decimal("75.00")
        with pytest.raises(RiskRefusal, match="drawdown"):
            _log(session, stake_pln=Decimal("5"))

    def test_the_breaker_can_be_overridden_and_says_so_on_the_pick(
        self, session: Session
    ) -> None:
        self._lose(session, 15, settled_at=_PLACED - timedelta(hours=1))
        pick = _log(session, stake_pln=Decimal("5"), override_risk=True)
        assert pick.risk_override is True
        assert pick.risk_note is not None and "drawdown" in pick.risk_note

    def test_custom_limits_are_honoured(self, session: Session) -> None:
        self._lose(session, 3, settled_at=_PLACED - timedelta(hours=1))
        tight = RiskLimits.for_bankroll(Decimal("100"))  # 15 PLN drawdown stop
        assert tight.max_drawdown_pln == Decimal("15")
        with pytest.raises(RiskRefusal, match="drawdown"):
            _log(session, stake_pln=Decimal("5"), limits=tight)

    def test_the_default_limits_are_the_agreed_ones(self) -> None:
        assert DEFAULT_LIMITS.max_drawdown_pln == Decimal("75")
