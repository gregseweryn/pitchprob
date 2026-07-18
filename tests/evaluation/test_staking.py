"""Staking simulation tests with hand-computed economics."""

from datetime import date

import pandas as pd
import pytest

from pitchprob.evaluation.staking import simulate_staking


def _candidates(rows: list[dict]) -> pd.DataFrame:
    base = {"date": date(2024, 1, 1)}
    return pd.DataFrame([{**base, **r} for r in rows])


class TestFlatStaking:
    def test_all_winning_bets(self) -> None:
        bets = _candidates(
            [
                {"probability": 0.60, "price": 2.0, "won": True},
                {"probability": 0.55, "price": 2.2, "won": True},
            ]
        )
        result = simulate_staking(bets, strategy="flat", ev_threshold=0.0)
        assert result.n_bets == 2
        # profit = (2.0-1) + (2.2-1) = 2.2 on 2 staked
        assert result.profit == pytest.approx(2.2)
        assert result.roi == pytest.approx(1.1)
        assert result.hit_rate == pytest.approx(1.0)

    def test_ev_threshold_filters(self) -> None:
        bets = _candidates(
            [
                {"probability": 0.50, "price": 2.0, "won": True},  # EV = 0
                {"probability": 0.60, "price": 2.0, "won": False},  # EV = +0.2
            ]
        )
        result = simulate_staking(bets, strategy="flat", ev_threshold=0.05)
        assert result.n_bets == 1
        assert result.profit == pytest.approx(-1.0)

    def test_no_qualifying_bets(self) -> None:
        bets = _candidates([{"probability": 0.4, "price": 2.0, "won": True}])
        result = simulate_staking(bets, strategy="flat", ev_threshold=0.05)
        assert result.n_bets == 0
        assert result.roi == 0.0

    def test_max_drawdown_hand_computed(self) -> None:
        # win +1, lose -1, lose -1, win +1 -> peak 1, trough -1 => drawdown 2
        bets = _candidates(
            [
                {"probability": 0.9, "price": 2.0, "won": True},
                {"probability": 0.9, "price": 2.0, "won": False},
                {"probability": 0.9, "price": 2.0, "won": False},
                {"probability": 0.9, "price": 2.0, "won": True},
            ]
        )
        result = simulate_staking(bets, strategy="flat", ev_threshold=0.0)
        assert result.max_drawdown == pytest.approx(2.0)


class TestKellyStaking:
    def test_compounding_hand_computed(self) -> None:
        # kelly f* = (0.6*2-1)/(2-1) = 0.2; quarter kelly = 0.05 of bankroll
        bets = _candidates(
            [
                {"probability": 0.6, "price": 2.0, "won": True},
                {"probability": 0.6, "price": 2.0, "won": True},
            ]
        )
        result = simulate_staking(
            bets, strategy="kelly", kelly_fraction=0.25, initial_bankroll=100.0
        )
        # bet1: stake 5, bankroll 105; bet2: stake 5.25, bankroll 110.25
        assert result.n_bets == 2
        assert result.final_bankroll == pytest.approx(110.25)

    def test_losses_shrink_stakes(self) -> None:
        bets = _candidates(
            [
                {"probability": 0.6, "price": 2.0, "won": False},
                {"probability": 0.6, "price": 2.0, "won": False},
            ]
        )
        result = simulate_staking(
            bets, strategy="kelly", kelly_fraction=0.25, initial_bankroll=100.0
        )
        assert result.final_bankroll == pytest.approx(100 * 0.95 * 0.95)


class TestClosingLineValue:
    def test_clv_computed_against_closing_probability(self) -> None:
        bets = _candidates(
            [
                # took 2.10 while fair closing prob is 0.5 (fair price 2.0):
                # CLV = 2.10 * 0.5 - 1 = +0.05
                {"probability": 0.55, "price": 2.10, "won": True, "closing_probability": 0.5},
            ]
        )
        result = simulate_staking(bets, strategy="flat", ev_threshold=0.0)
        assert result.mean_clv == pytest.approx(0.05)

    def test_clv_absent_when_no_closing_data(self) -> None:
        bets = _candidates([{"probability": 0.55, "price": 2.10, "won": True}])
        result = simulate_staking(bets, strategy="flat", ev_threshold=0.0)
        assert result.mean_clv is None


class TestGrossReturnSettlement:
    """Asian handicap and totals settle with pushes and half-wins that a
    boolean ``won`` cannot express: an optional ``gross_return`` column
    (gross return per unit stake, as produced by ``betting.settlement``)
    carries the exact economics (ADR 0010)."""

    def test_push_returns_stake_and_is_not_a_win(self) -> None:
        bets = _candidates(
            [{"probability": 0.6, "price": 2.0, "gross_return": 1.0}]
        )
        result = simulate_staking(bets, strategy="flat", ev_threshold=0.0)
        assert result.n_bets == 1
        assert result.profit == pytest.approx(0.0)
        assert result.hit_rate == pytest.approx(0.0)

    def test_half_win_hand_computed(self) -> None:
        # Quarter-line half win at price 2.0: gross (2.0 + 1) / 2 = 1.5 →
        # profit +0.5 on 1 staked.
        bets = _candidates(
            [{"probability": 0.6, "price": 2.0, "gross_return": 1.5}]
        )
        result = simulate_staking(bets, strategy="flat", ev_threshold=0.0)
        assert result.profit == pytest.approx(0.5)
        assert result.hit_rate == pytest.approx(1.0)

    def test_full_win_parity_with_won_flag(self) -> None:
        by_flag = _candidates([{"probability": 0.6, "price": 2.2, "won": True}])
        by_gross = _candidates(
            [{"probability": 0.6, "price": 2.2, "gross_return": 2.2}]
        )
        flag_result = simulate_staking(by_flag, strategy="flat", ev_threshold=0.0)
        gross_result = simulate_staking(by_gross, strategy="flat", ev_threshold=0.0)
        assert gross_result.profit == pytest.approx(flag_result.profit)
        assert gross_result.hit_rate == pytest.approx(flag_result.hit_rate)

    def test_nan_gross_return_falls_back_to_won(self) -> None:
        bets = _candidates(
            [
                {"probability": 0.6, "price": 2.0, "won": True,
                 "gross_return": float("nan")},
                {"probability": 0.6, "price": 2.0, "won": False,
                 "gross_return": 1.0},
            ]
        )
        result = simulate_staking(bets, strategy="flat", ev_threshold=0.0)
        # Row 1 settles by flag (+1.0); row 2 by gross (push, 0.0).
        assert result.profit == pytest.approx(1.0)
        assert result.n_bets == 2

    def test_kelly_compounds_gross_returns(self) -> None:
        # f* = (0.6*2-1)/1 = 0.2, quarter = 0.05: stake 5 on 100. Half-win
        # at gross 1.5 → +2.5 → bankroll 102.5.
        bets = _candidates(
            [{"probability": 0.6, "price": 2.0, "gross_return": 1.5}]
        )
        result = simulate_staking(
            bets, strategy="kelly", kelly_fraction=0.25, initial_bankroll=100.0
        )
        assert result.final_bankroll == pytest.approx(102.5)


class TestBetLog:
    """Per-bet records back the block-bootstrap confidence intervals: the
    harness needs (date, stake, pnl, clv) for every settled bet."""

    def test_log_length_and_economics(self) -> None:
        bets = _candidates(
            [
                {"probability": 0.9, "price": 2.0, "won": True,
                 "closing_probability": 0.5},
                {"probability": 0.9, "price": 2.0, "won": False},
                {"probability": 0.1, "price": 2.0, "won": True},  # filtered out
            ]
        )
        result = simulate_staking(bets, strategy="flat", ev_threshold=0.0)
        log = result.bet_log
        assert log is not None
        assert len(log) == result.n_bets == 2
        assert log["pnl"].tolist() == pytest.approx([1.0, -1.0])
        assert log["stake"].tolist() == pytest.approx([1.0, 1.0])
        # CLV per bet where closing data exists: 2.0 * 0.5 - 1 = 0.
        assert log["clv"].iloc[0] == pytest.approx(0.0)
        assert log["clv"].isna().iloc[1]

    def test_passthrough_columns_survive(self) -> None:
        bets = _candidates(
            [{"probability": 0.9, "price": 2.0, "won": True, "market": "ah"}]
        )
        result = simulate_staking(bets, strategy="flat", ev_threshold=0.0)
        assert result.bet_log is not None
        assert result.bet_log["market"].iloc[0] == "ah"
