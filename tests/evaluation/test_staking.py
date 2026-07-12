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
