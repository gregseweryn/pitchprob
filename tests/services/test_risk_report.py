"""The weekly "what the tape says" report (Phase 3, ADR 0015).

The report's job is to be readable when the answer is "not enough data" —
which it will be for most of the season. So the load-bearing tests are the
small-sample ones: a mean over three bets in one week is a point estimate,
and a bootstrap CI over a single block is a straight line, not an interval.
Publishing either as evidence is the failure this report exists to avoid.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from pitchprob.data.orm import Base, Pick
from pitchprob.services.risk_report import MIN_BLOCKS_FOR_CI, weekly_report

_NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)  # a Monday


def _pick(
    session: Session,
    *,
    days_ago: int,
    clv_exec: float | None = None,
    clv_sharp: float | None = None,
    stake: str = "5",
    gross: str | None = None,
    tax_free: bool = False,
    bookmaker: str = "betclic",
    risk_override: bool = False,
    risk_note: str | None = None,
) -> Pick:
    when = _NOW - timedelta(days=days_ago)
    pick = Pick(
        created_at=when,
        event_id=f"ev{days_ago}",
        home_team="Arsenal",
        away_team="Coventry City",
        kickoff_utc=when,
        market="ou",
        selection="over",
        line=Decimal("3.0"),
        bookmaker=bookmaker,
        stake_pln=Decimal(stake),
        price_quoted=Decimal("2.10"),
        tax_free=tax_free,
        price_effective=Decimal("2.10") if tax_free else Decimal("1.848"),
        placed_at=when,
        clv_exec=clv_exec,
        clv_sharp=clv_sharp,
        risk_override=risk_override,
        risk_note=risk_note,
    )
    if gross is not None:
        pick.gross_return_pln = Decimal(gross)
        pick.settled_at = when + timedelta(hours=3)
    session.add(pick)
    session.flush()
    return pick


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class TestEmptyLedger:
    def test_an_empty_ledger_reports_rather_than_failing(
        self, session: Session
    ) -> None:
        result = weekly_report(session, now=_NOW)
        assert result.metrics["window"]["n_picks"] == 0
        assert result.metrics["clv"]["n"] == 0
        assert result.metrics["clv"]["exec_ci"] is None
        assert "no picks" in result.report.lower()

    def test_an_untouched_bankroll_shows_no_drawdown(
        self, session: Session
    ) -> None:
        result = weekly_report(session, now=_NOW)
        assert result.metrics["drawdown"]["drawdown_pln"] == 0.0
        assert result.metrics["drawdown"]["breaker_tripped"] is False


class TestSmallSampleHonesty:
    def test_three_bets_in_one_week_get_a_mean_but_no_interval(
        self, session: Session
    ) -> None:
        """One ISO week is one bootstrap block. Resampling a single block
        returns that block every time, so the "95% CI" would be a point
        with zero width — the most confident-looking output in the whole
        system, produced by the least evidence."""
        for offset in (0, 1, 2):
            _pick(session, days_ago=offset, clv_exec=0.02, clv_sharp=-0.01)
        result = weekly_report(session, now=_NOW)
        clv = result.metrics["clv"]
        assert clv["n"] == 3
        assert clv["mean_exec"] == pytest.approx(0.02)
        assert clv["mean_sharp"] == pytest.approx(-0.01)
        assert clv["exec_ci"] is None
        assert clv["n_blocks"] < MIN_BLOCKS_FOR_CI
        assert "not a finding" in result.report

    def test_enough_weeks_earns_an_interval(self, session: Session) -> None:
        # one bet a week for MIN_BLOCKS_FOR_CI weeks
        for week in range(MIN_BLOCKS_FOR_CI):
            _pick(session, days_ago=7 * week, clv_exec=0.02, clv_sharp=-0.01)
        result = weekly_report(session, now=_NOW, n_boot=200)
        clv = result.metrics["clv"]
        assert clv["n_blocks"] >= MIN_BLOCKS_FOR_CI
        assert clv["exec_ci"] is not None
        assert clv["exec_ci"]["lo"] <= clv["mean_exec"] <= clv["exec_ci"]["hi"]

    def test_the_shopping_component_is_the_paired_difference(
        self, session: Session
    ) -> None:
        """exec - sharp per bet, paired: the venue/promo value that the ADR
        0011 two-label design exists to isolate."""
        _pick(session, days_ago=0, clv_exec=0.05, clv_sharp=-0.01)
        _pick(session, days_ago=1, clv_exec=0.03, clv_sharp=0.01)
        clv = weekly_report(session, now=_NOW).metrics["clv"]
        assert clv["mean_shopping"] == pytest.approx(0.04)  # (0.06 + 0.02)/2


class TestWindow:
    def test_the_window_covers_seven_days_but_clv_uses_the_whole_ledger(
        self, session: Session
    ) -> None:
        """CLV needs every bet it can get; the money section is about the
        week just gone. Mixing the two horizons is how a bad week gets
        reported as a bad season."""
        _pick(session, days_ago=2, clv_exec=0.02, stake="5", gross="0")
        _pick(session, days_ago=30, clv_exec=0.10, stake="5", gross="10.50")
        result = weekly_report(session, now=_NOW)
        assert result.metrics["window"]["n_picks"] == 1
        assert result.metrics["window"]["staked_pln"] == pytest.approx(5.0)
        assert result.metrics["clv"]["n"] == 2

    def test_window_profit_is_hand_computed(self, session: Session) -> None:
        # winner: 10.50 gross on a 5 PLN stake = +5.50; loser: -5.00
        _pick(session, days_ago=1, stake="5", gross="10.50")
        _pick(session, days_ago=2, stake="5", gross="0")
        window = weekly_report(session, now=_NOW).metrics["window"]
        assert window["profit_pln"] == pytest.approx(0.50)
        assert window["n_settled"] == 2


class TestTaxFreeAllowance:
    def test_betclic_allowance_reflects_tax_free_stakes_only(
        self, session: Session
    ) -> None:
        _pick(session, days_ago=1, stake="5", tax_free=True)
        _pick(session, days_ago=2, stake="5", tax_free=False)
        allowance = weekly_report(session, now=_NOW).metrics["tax_free"]
        assert allowance["betclic"]["used_pln"] == pytest.approx(5.0)
        assert allowance["betclic"]["remaining_pln"] == pytest.approx(995.0)

    def test_a_book_with_no_tax_free_picks_still_reports_its_full_allowance(
        self, session: Session
    ) -> None:
        result = weekly_report(session, now=_NOW)
        assert result.metrics["tax_free"]["betclic"]["remaining_pln"] == pytest.approx(
            1000.0
        )


class TestOverridesAndBreaker:
    def test_overridden_picks_are_listed_with_their_reason(
        self, session: Session
    ) -> None:
        _pick(
            session, days_ago=1, risk_override=True,
            risk_note="max_match_stake: 7 would exceed the limit 5",
        )
        result = weekly_report(session, now=_NOW)
        overrides = result.metrics["overrides"]
        assert len(overrides) == 1
        assert "max_match_stake" in overrides[0]["risk_note"]
        assert "override" in result.report.lower()

    def test_a_clean_week_says_so(self, session: Session) -> None:
        _pick(session, days_ago=1)
        result = weekly_report(session, now=_NOW)
        assert result.metrics["overrides"] == []

    def test_the_breaker_state_is_reported_against_the_threshold(
        self, session: Session
    ) -> None:
        # 15 settled losers of 5 PLN = 75 PLN drawdown = the stop
        for index in range(15):
            _pick(session, days_ago=index + 1, stake="5", gross="0")
        drawdown = weekly_report(session, now=_NOW).metrics["drawdown"]
        assert drawdown["drawdown_pln"] == pytest.approx(75.0)
        assert drawdown["breaker_tripped"] is True
        assert drawdown["limit_pln"] == pytest.approx(75.0)
