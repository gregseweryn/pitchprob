"""Risk-layer primitives — hand-computed thresholds (Phase 3, ADR 0015).

Every number here is checked by eye against the agreed parameters: 500 PLN
notional bankroll, flat 2-5 PLN stakes, one bet per fixture. The cases that
matter most are the boundaries: a limit that fires one grosz early blocks a
legal bet, one that fires a grosz late is not a limit.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from pitchprob.betting.risk import (
    DEFAULT_LIMITS,
    DrawdownState,
    ExposureState,
    RiskLimits,
    breaker_tripped,
    check_exposure,
    drawdown_state,
)

_T0 = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def _state(**kwargs) -> ExposureState:
    base = {
        "match_stake_pln": Decimal("0"),
        "daily_stake_pln": Decimal("0"),
        "open_picks": 0,
    }
    return ExposureState(**{**base, **kwargs})


class TestAgreedParameters:
    """The limits are fixed here, in advance, so they cannot be tuned to a
    result later — the same discipline the experiment registry uses."""

    def test_defaults_match_the_measurement_profile(self) -> None:
        assert DEFAULT_LIMITS.bankroll_pln == Decimal("500")
        assert DEFAULT_LIMITS.min_stake_pln == Decimal("2")
        assert DEFAULT_LIMITS.max_stake_pln == Decimal("5")
        # one bet per fixture: the per-match cap equals the max single stake
        assert DEFAULT_LIMITS.max_match_stake_pln == Decimal("5")
        assert DEFAULT_LIMITS.max_daily_stake_pln == Decimal("25")
        assert DEFAULT_LIMITS.max_open_picks == 15
        assert DEFAULT_LIMITS.max_drawdown_pln == Decimal("75")

    def test_drawdown_limit_is_fifteen_percent_of_bankroll(self) -> None:
        assert DEFAULT_LIMITS.max_drawdown_pln == (
            DEFAULT_LIMITS.bankroll_pln * Decimal("0.15")
        )


class TestStakeBand:
    def test_a_stake_inside_the_band_passes(self) -> None:
        assert check_exposure(_state(), Decimal("5"), limits=DEFAULT_LIMITS) == []

    def test_a_stake_above_the_band_is_refused(self) -> None:
        """It trips the per-match cap too, because that cap *equals* the
        single-stake cap under the one-bet-per-fixture rule. The redundancy
        is a consequence of the agreed profile, not a bug — and suppressing
        it would hide the per-match limit whenever someone widens the band."""
        violations = check_exposure(_state(), Decimal("5.01"))
        by_limit = {v.limit: v for v in violations}
        assert set(by_limit) == {"max_stake", "max_match_stake"}
        assert by_limit["max_stake"].attempted == Decimal("5.01")
        assert by_limit["max_stake"].allowed == Decimal("5")

    def test_a_stake_below_the_band_is_refused(self) -> None:
        """Flat staking is the design: a 0.50 PLN bet is not a smaller bet,
        it is a different experiment."""
        (violation,) = check_exposure(_state(), Decimal("1.99"))
        assert violation.limit == "min_stake"

    def test_exactly_the_band_edges_are_allowed(self) -> None:
        assert check_exposure(_state(), Decimal("2")) == []
        assert check_exposure(_state(), Decimal("5")) == []


class TestPerMatchLimit:
    def test_a_second_bet_on_the_same_fixture_is_refused(self) -> None:
        """The correlation control: two bets on one match are not two
        observations, and the weekly block bootstrap cannot see the
        dependence inside a fixture."""
        (violation,) = check_exposure(
            _state(match_stake_pln=Decimal("5")), Decimal("2")
        )
        assert violation.limit == "max_match_stake"
        assert violation.attempted == Decimal("7")  # 5 already + 2 candidate
        assert violation.allowed == Decimal("5")

    def test_a_fixture_at_exactly_the_cap_still_admits_nothing_more(
        self,
    ) -> None:
        violations = check_exposure(
            _state(match_stake_pln=Decimal("3")), Decimal("2")
        )
        assert violations == []  # 3 + 2 = 5, exactly the cap
        (violation,) = check_exposure(
            _state(match_stake_pln=Decimal("3")), Decimal("2.01")
        )
        assert violation.limit == "max_match_stake"


class TestDailyLimit:
    def test_the_sixth_five_pln_bet_of_the_day_is_refused(self) -> None:
        # 25 PLN daily cap / 5 PLN flat = five bets
        assert check_exposure(_state(daily_stake_pln=Decimal("20")), Decimal("5")) == []
        (violation,) = check_exposure(
            _state(daily_stake_pln=Decimal("25")), Decimal("2")
        )
        assert violation.limit == "max_daily_stake"
        assert violation.attempted == Decimal("27")
        assert violation.allowed == Decimal("25")


class TestOpenPickLimit:
    def test_open_picks_at_the_cap_refuse_another(self) -> None:
        assert check_exposure(_state(open_picks=14), Decimal("5")) == []
        (violation,) = check_exposure(_state(open_picks=15), Decimal("5"))
        assert violation.limit == "max_open_picks"
        assert violation.attempted == 16
        assert violation.allowed == 15


class TestMultipleViolations:
    def test_every_breached_limit_is_reported_not_just_the_first(self) -> None:
        """An operator who fixes one limit only to hit the next learns
        nothing; the refusal names all of them at once."""
        violations = check_exposure(
            _state(
                match_stake_pln=Decimal("5"),
                daily_stake_pln=Decimal("25"),
                open_picks=15,
            ),
            Decimal("6"),
        )
        assert {v.limit for v in violations} == {
            "max_stake", "max_match_stake", "max_daily_stake", "max_open_picks",
        }


class TestDrawdown:
    """Equity = bankroll + cumulative realized profit, in settlement order.

    Profit per settled pick is ``gross_return - stake``: a 5 PLN bet at
    effective 1.76 that wins returns 8.80 gross, i.e. +3.80; a loser is
    -5.00.
    """

    def test_a_losing_run_measures_from_the_peak(self) -> None:
        # +3.80, then three losers of 5.00 => equity 500 -> 503.80 -> 488.80
        state = drawdown_state(
            [
                (_T0, Decimal("3.80")),
                (_T0 + timedelta(days=1), Decimal("-5.00")),
                (_T0 + timedelta(days=2), Decimal("-5.00")),
                (_T0 + timedelta(days=3), Decimal("-5.00")),
            ],
            bankroll=Decimal("500"),
        )
        assert state.peak_equity_pln == Decimal("503.80")
        assert state.equity_pln == Decimal("488.80")
        assert state.drawdown_pln == Decimal("15.00")

    def test_a_new_high_resets_the_drawdown_to_zero(self) -> None:
        state = drawdown_state(
            [
                (_T0, Decimal("-5.00")),
                (_T0 + timedelta(days=1), Decimal("20.00")),
            ],
            bankroll=Decimal("500"),
        )
        assert state.peak_equity_pln == Decimal("515.00")
        assert state.drawdown_pln == Decimal("0")

    def test_settlement_order_decides_the_peak_not_input_order(self) -> None:
        """Realized P&L moves when a bet settles, not when it was placed;
        feeding rows out of order must not invent a peak."""
        rows = [
            (_T0 + timedelta(days=3), Decimal("-5.00")),
            (_T0, Decimal("20.00")),
        ]
        state = drawdown_state(rows, bankroll=Decimal("500"))
        assert state.peak_equity_pln == Decimal("520.00")
        assert state.drawdown_pln == Decimal("5.00")

    def test_an_empty_ledger_sits_at_the_bankroll_with_no_drawdown(self) -> None:
        state = drawdown_state([], bankroll=Decimal("500"))
        assert state.equity_pln == Decimal("500")
        assert state.peak_equity_pln == Decimal("500")
        assert state.drawdown_pln == Decimal("0")

    def test_the_starting_bankroll_is_the_first_peak(self) -> None:
        """A ledger that only ever loses is still measured against 500, not
        against its own best moment after the losses began."""
        state = drawdown_state(
            [(_T0, Decimal("-5.00")), (_T0 + timedelta(days=1), Decimal("-5.00"))],
            bankroll=Decimal("500"),
        )
        assert state.peak_equity_pln == Decimal("500")
        assert state.drawdown_pln == Decimal("10.00")


class TestBreaker:
    def _at(self, drawdown: str) -> DrawdownState:
        return DrawdownState(
            equity_pln=Decimal("500") - Decimal(drawdown),
            peak_equity_pln=Decimal("500"),
            drawdown_pln=Decimal(drawdown),
        )

    def test_below_the_threshold_the_breaker_stays_open(self) -> None:
        assert breaker_tripped(self._at("74.99"), limits=DEFAULT_LIMITS) is False

    def test_at_the_threshold_the_breaker_trips(self) -> None:
        """At exactly 75.00 PLN — 15% of bankroll — betting stops. The
        boundary is inclusive: a limit reached is a limit hit."""
        assert breaker_tripped(self._at("75.00"), limits=DEFAULT_LIMITS) is True

    def test_a_custom_bankroll_scales_the_default_threshold(self) -> None:
        limits = RiskLimits.for_bankroll(Decimal("1000"))
        assert limits.max_drawdown_pln == Decimal("150")
        assert limits.max_daily_stake_pln == Decimal("50")
        # the stake band is a program parameter, not a fraction of bankroll
        assert limits.max_stake_pln == Decimal("5")


class TestLimitsValidation:
    def test_a_daily_cap_below_the_single_stake_cap_is_incoherent(self) -> None:
        with pytest.raises(ValueError, match="daily"):
            RiskLimits(
                bankroll_pln=Decimal("500"),
                min_stake_pln=Decimal("2"),
                max_stake_pln=Decimal("5"),
                max_match_stake_pln=Decimal("5"),
                max_daily_stake_pln=Decimal("4"),
                max_open_picks=15,
                max_drawdown_pln=Decimal("75"),
            )

    def test_a_match_cap_below_the_minimum_stake_blocks_every_bet(self) -> None:
        with pytest.raises(ValueError, match="match"):
            RiskLimits(
                bankroll_pln=Decimal("500"),
                min_stake_pln=Decimal("2"),
                max_stake_pln=Decimal("5"),
                max_match_stake_pln=Decimal("1"),
                max_daily_stake_pln=Decimal("25"),
                max_open_picks=15,
                max_drawdown_pln=Decimal("75"),
            )
