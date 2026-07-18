"""Realized settlement from final scores (ADR 0010).

The contract is gross return per unit stake: win → price, push → 1, loss → 0,
with quarter lines split across the adjacent half lines. The strongest test
here is cross-module: expected settlement under a score matrix must equal the
(already property-tested) probability-side ``AhOutcome.expected_return`` /
``totals`` numbers cell-for-cell.
"""

from decimal import Decimal

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pitchprob.betting.settlement import (
    settle_1x2,
    settle_asian_handicap,
    settle_totals,
)
from pitchprob.markets import asian_handicap, totals


class TestSettle1x2:
    def test_winning_selection_returns_price(self) -> None:
        assert settle_1x2("home", 2, 1, 2.5) == pytest.approx(2.5)
        assert settle_1x2("draw", 1, 1, 3.2) == pytest.approx(3.2)
        assert settle_1x2("away", 0, 2, 4.0) == pytest.approx(4.0)

    def test_losing_selection_returns_zero(self) -> None:
        assert settle_1x2("away", 2, 1, 4.0) == pytest.approx(0.0)
        assert settle_1x2("draw", 2, 1, 3.2) == pytest.approx(0.0)

    def test_rejects_unknown_selection(self) -> None:
        with pytest.raises(ValueError):
            settle_1x2("banker", 2, 1, 2.0)  # type: ignore[arg-type]


class TestSettleTotals:
    def test_half_line_win_and_loss(self) -> None:
        assert settle_totals("over", 3, Decimal("2.5"), 1.9) == pytest.approx(1.9)
        assert settle_totals("under", 3, Decimal("2.5"), 1.9) == pytest.approx(0.0)
        assert settle_totals("under", 2, Decimal("2.5"), 1.9) == pytest.approx(1.9)

    def test_integer_line_pushes_on_exact_total(self) -> None:
        assert settle_totals("over", 2, Decimal("2"), 1.8) == pytest.approx(1.0)
        assert settle_totals("under", 2, Decimal("2"), 2.1) == pytest.approx(1.0)

    def test_quarter_line_splits_the_stake(self) -> None:
        # Over 2.25 with total 2: half at 2.0 pushes (1.0), half at 2.5
        # loses (0.0) → 0.5 gross.
        assert settle_totals("over", 2, Decimal("2.25"), 2.0) == pytest.approx(0.5)
        # Under 2.75 with total 3: half at 2.5 loses, half at 3.0 pushes.
        assert settle_totals("under", 3, Decimal("2.75"), 2.0) == pytest.approx(0.5)
        # Over 2.75 with total 3: half at 2.5 wins, half at 3.0 pushes.
        assert settle_totals("over", 3, Decimal("2.75"), 2.0) == pytest.approx(1.5)

    def test_rejects_non_quarter_multiple_line(self) -> None:
        with pytest.raises(ValueError):
            settle_totals("over", 2, Decimal("2.3"), 1.9)

    def test_rejects_negative_total(self) -> None:
        with pytest.raises(ValueError):
            settle_totals("over", -1, Decimal("2.5"), 1.9)


class TestSettleAsianHandicap:
    def test_half_line_home_win(self) -> None:
        # Home -0.5: 1-0 covers (margin 1 - 0.5 > 0).
        assert settle_asian_handicap("home", 1, 0, Decimal("-0.5"), 2.0) == (
            pytest.approx(2.0)
        )

    def test_integer_line_push(self) -> None:
        # Home -1: 1-0 lands exactly on the line.
        assert settle_asian_handicap("home", 1, 0, Decimal("-1"), 2.0) == (
            pytest.approx(1.0)
        )

    def test_quarter_line_half_win(self) -> None:
        # Home -0.75 wins 1-0: half at -0.5 wins (2.0), half at -1 pushes
        # (1.0) → (price + 1) / 2.
        assert settle_asian_handicap("home", 1, 0, Decimal("-0.75"), 2.0) == (
            pytest.approx(1.5)
        )

    def test_quarter_line_half_loss_for_away(self) -> None:
        # Away at home-line -0.75 (away +0.75) losing 0-1: half at +1
        # pushes, half at +0.5 loses → 0.5 gross.
        assert settle_asian_handicap("away", 1, 0, Decimal("-0.75"), 2.0) == (
            pytest.approx(0.5)
        )

    def test_away_mirrors_home_line(self) -> None:
        # Away at home-line +0.5 means away gives half a goal.
        assert settle_asian_handicap("away", 0, 1, Decimal("0.5"), 1.9) == (
            pytest.approx(1.9)
        )
        assert settle_asian_handicap("away", 1, 1, Decimal("0.5"), 1.9) == (
            pytest.approx(0.0)
        )

    def test_rejects_non_quarter_multiple_line(self) -> None:
        with pytest.raises(ValueError):
            settle_asian_handicap("home", 1, 0, Decimal("-0.4"), 2.0)


def _small_matrices() -> st.SearchStrategy[np.ndarray]:
    """4x4 normalized score matrices — enough goals to exercise every
    settlement branch."""

    def normalize(cells: list[float]) -> np.ndarray:
        m = np.asarray(cells, dtype=np.float64).reshape(4, 4) + 1e-9
        return m / m.sum()

    return st.lists(
        st.floats(0.0, 1.0, allow_nan=False), min_size=16, max_size=16
    ).map(normalize)


_AH_LINES = [Decimal(n) / 4 for n in range(-8, 9)]  # -2.00 … +2.00 by 0.25
_TOTALS_LINES = [Decimal(n) / 2 for n in range(1, 8)]  # 0.5 … 3.5 by 0.5


class TestConsistencyWithMarketsModule:
    """Expected realized settlement under a score matrix must equal the
    probability-side numbers from ``pitchprob.markets`` exactly."""

    @given(
        matrix=_small_matrices(),
        line_index=st.integers(0, len(_AH_LINES) - 1),
        side=st.sampled_from(["home", "away"]),
        price=st.floats(1.2, 5.0, allow_nan=False),
    )
    @settings(max_examples=60, deadline=None)
    def test_asian_handicap_expectation_matches(
        self, matrix: np.ndarray, line_index: int, side: str, price: float
    ) -> None:
        line = _AH_LINES[line_index]
        expected = asian_handicap(matrix, line, side).expected_return(price)  # type: ignore[arg-type]
        realized = sum(
            float(matrix[h, a])
            * settle_asian_handicap(side, h, a, line, price)  # type: ignore[arg-type]
            for h in range(4)
            for a in range(4)
        )
        assert realized == pytest.approx(expected, abs=1e-12)

    @given(
        matrix=_small_matrices(),
        line_index=st.integers(0, len(_TOTALS_LINES) - 1),
        selection=st.sampled_from(["over", "under"]),
        price=st.floats(1.2, 5.0, allow_nan=False),
    )
    @settings(max_examples=60, deadline=None)
    def test_totals_expectation_matches(
        self, matrix: np.ndarray, line_index: int, selection: str, price: float
    ) -> None:
        line = _TOTALS_LINES[line_index]
        market = totals(matrix, line)
        win = market.over if selection == "over" else market.under
        expected = win * price + market.push * 1.0
        realized = sum(
            float(matrix[h, a])
            * settle_totals(selection, h + a, line, price)  # type: ignore[arg-type]
            for h in range(4)
            for a in range(4)
        )
        assert realized == pytest.approx(expected, abs=1e-12)

    @given(
        side=st.sampled_from(["home", "away"]),
        line_index=st.integers(0, len(_AH_LINES) - 1),
        ft_home=st.integers(0, 6),
        ft_away=st.integers(0, 6),
        price=st.floats(1.01, 20.0, allow_nan=False),
    )
    @settings(max_examples=60, deadline=None)
    def test_settlement_bounded_by_stake_and_price(
        self, side: str, line_index: int, ft_home: int, ft_away: int, price: float
    ) -> None:
        gross = settle_asian_handicap(
            side, ft_home, ft_away, _AH_LINES[line_index], price  # type: ignore[arg-type]
        )
        assert 0.0 <= gross <= price
