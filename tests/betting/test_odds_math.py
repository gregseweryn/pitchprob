"""Odds mathematics: implied probabilities, overround removal, fair odds."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pitchprob.betting.odds_math import (
    fair_odds,
    implied_probabilities,
    overround,
    remove_overround_multiplicative,
    remove_overround_shin,
)


def prices_strategy(n_min: int = 2, n_max: int = 5):
    return st.lists(
        st.floats(min_value=1.05, max_value=50.0),
        min_size=n_min,
        max_size=n_max,
    )


class TestImplied:
    def test_hand_computed(self) -> None:
        assert implied_probabilities([2.0, 4.0]) == pytest.approx([0.5, 0.25])

    def test_overround(self) -> None:
        # classic 1X2 book: 2.0 / 3.6 / 3.6 -> ~5.56% margin
        assert overround([2.0, 3.6, 3.6]) == pytest.approx(0.05555, abs=1e-4)

    def test_rejects_invalid_prices(self) -> None:
        with pytest.raises(ValueError):
            implied_probabilities([2.0, 0.99])


class TestMultiplicative:
    def test_hand_computed(self) -> None:
        probs = remove_overround_multiplicative([2.0, 3.6, 3.6])
        assert probs[0] == pytest.approx(0.5 / (0.5 + 2 / 3.6))
        assert sum(probs) == pytest.approx(1.0)

    def test_fair_book_unchanged(self) -> None:
        probs = remove_overround_multiplicative([2.0, 4.0, 4.0])
        assert probs == pytest.approx([0.5, 0.25, 0.25])

    @given(prices_strategy())
    def test_sums_to_one_and_preserves_order(self, prices: list[float]) -> None:
        probs = remove_overround_multiplicative(prices)
        assert sum(probs) == pytest.approx(1.0)
        # shorter price (bigger implied) => bigger probability
        for i in range(len(prices) - 1):
            if prices[i] < prices[i + 1]:
                assert probs[i] > probs[i + 1]


class TestShin:
    def test_fair_book_unchanged(self) -> None:
        probs = remove_overround_shin([2.0, 4.0, 4.0])
        assert probs == pytest.approx([0.5, 0.25, 0.25], abs=1e-9)

    def test_corrects_favourite_longshot_bias(self) -> None:
        """Shin shifts probability mass toward favourites relative to the
        multiplicative method (longshots carry more of the margin)."""
        prices = [1.30, 5.75, 9.00]  # heavy favourite book with margin
        shin = remove_overround_shin(prices)
        mult = remove_overround_multiplicative(prices)
        assert shin[0] > mult[0]  # favourite
        assert shin[2] < mult[2]  # longshot

    @given(prices_strategy(n_min=3, n_max=3))
    def test_sums_to_one(self, prices: list[float]) -> None:
        probs = remove_overround_shin(prices)
        assert sum(probs) == pytest.approx(1.0, abs=1e-6)
        assert all(p >= 0 for p in probs)


class TestFairOdds:
    def test_inverse_of_probability(self) -> None:
        assert fair_odds([0.5, 0.25, 0.25]) == pytest.approx([2.0, 4.0, 4.0])

    def test_rejects_zero(self) -> None:
        with pytest.raises(ValueError):
            fair_odds([0.5, 0.0])
