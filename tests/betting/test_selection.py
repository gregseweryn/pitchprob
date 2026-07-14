"""Vig-aware bet selection tests (ADR 0006).

The blend is a log-linear pool: with weight w, p_bet ∝ p_model^w * p_market^(1-w).
Hand-computed cases pin the arithmetic; behavioral tests pin the two guarantees
that kill the M2 longshot trap — market anchoring and the price cap.
"""

import numpy as np
import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st

from pitchprob.betting.selection import blend_probabilities, select_value_bets


class TestBlendProbabilities:
    def test_weight_zero_returns_market(self) -> None:
        model = np.array([0.6, 0.25, 0.15])
        market = np.array([0.5, 0.3, 0.2])
        assert blend_probabilities(model, market, weight=0.0) == pytest.approx(market)

    def test_weight_one_returns_model(self) -> None:
        model = np.array([0.6, 0.25, 0.15])
        market = np.array([0.5, 0.3, 0.2])
        assert blend_probabilities(model, market, weight=1.0) == pytest.approx(model)

    def test_hand_computed_half_weight(self) -> None:
        model = np.array([0.8, 0.2])
        market = np.array([0.5, 0.5])
        raw = np.sqrt(model * market)  # w=0.5 log-linear pool
        expected = raw / raw.sum()
        assert blend_probabilities(model, market, weight=0.5) == pytest.approx(expected)

    def test_blend_lies_between_inputs(self) -> None:
        model = np.array([0.7, 0.2, 0.1])
        market = np.array([0.45, 0.3, 0.25])
        blended = blend_probabilities(model, market, weight=0.4)
        for i in range(3):
            lo, hi = sorted((model[i], market[i]))
            assert lo - 1e-9 <= blended[i] <= hi + 1e-9

    @given(
        st.lists(st.floats(min_value=0.01, max_value=1.0), min_size=2, max_size=4),
        st.lists(st.floats(min_value=0.01, max_value=1.0), min_size=2, max_size=4),
        st.floats(min_value=0.0, max_value=1.0),
    )
    def test_always_a_distribution(self, m: list[float], q: list[float], w: float) -> None:
        n = min(len(m), len(q))
        model = np.array(m[:n]) / sum(m[:n])
        market = np.array(q[:n]) / sum(q[:n])
        blended = blend_probabilities(model, market, weight=w)
        assert blended.sum() == pytest.approx(1.0)
        assert (blended >= 0).all()

    def test_rejects_bad_weight(self) -> None:
        with pytest.raises(ValueError):
            blend_probabilities(np.array([0.5, 0.5]), np.array([0.5, 0.5]), weight=1.2)


def _candidates(rows: list[dict]) -> pd.DataFrame:
    base = {"date": "2024-01-01", "selection": "home"}
    return pd.DataFrame([{**base, **r} for r in rows])


class TestSelectValueBets:
    def test_blended_ev_gates_selection(self) -> None:
        # model 0.55 vs market 0.45 at price 2.0: naive EV +0.10 qualifies,
        # blended (w=0.4) p_bet ≈ 0.49 -> EV ≈ -0.02 does not
        rows = _candidates(
            [{"probability": 0.55, "market_probability": 0.45, "price": 2.0}]
        )
        naive = select_value_bets(
            rows, blend_weight=1.0, ev_threshold=0.03, max_price=100.0
        )
        blended = select_value_bets(
            rows, blend_weight=0.4, ev_threshold=0.03, max_price=100.0
        )
        assert len(naive) == 1
        assert len(blended) == 0

    def test_price_cap_is_hard(self) -> None:
        rows = _candidates(
            [{"probability": 0.30, "market_probability": 0.28, "price": 9.0}]
        )
        kept = select_value_bets(rows, blend_weight=1.0, ev_threshold=0.0, max_price=8.0)
        assert len(kept) == 0

    def test_qualifying_bet_is_annotated(self) -> None:
        rows = _candidates(
            [{"probability": 0.60, "market_probability": 0.50, "price": 2.10}]
        )
        kept = select_value_bets(rows, blend_weight=0.5, ev_threshold=0.02, max_price=8.0)
        assert len(kept) == 1
        bet = kept.iloc[0]
        assert 0.50 < bet["p_bet"] < 0.60  # anchored between market and model
        assert bet["expected_value"] == pytest.approx(bet["p_bet"] * 2.10 - 1)
        assert bet["kelly_fraction"] > 0

    def test_missing_market_probability_falls_back_to_model(self) -> None:
        rows = _candidates(
            [{"probability": 0.60, "market_probability": float("nan"), "price": 2.10}]
        )
        kept = select_value_bets(rows, blend_weight=0.4, ev_threshold=0.02, max_price=8.0)
        assert len(kept) == 1
        assert kept.iloc[0]["p_bet"] == pytest.approx(0.60)

    def test_empty_input_returns_empty_annotated_frame(self) -> None:
        kept = select_value_bets(
            _candidates([]), blend_weight=0.4, ev_threshold=0.02, max_price=8.0
        )
        assert len(kept) == 0
        assert {"p_bet", "expected_value", "kelly_fraction"} <= set(kept.columns)
