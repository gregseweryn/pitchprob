"""Expected value and Kelly staking."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pitchprob.betting.staking import expected_value, kelly_fraction


class TestExpectedValue:
    def test_positive_edge(self) -> None:
        assert expected_value(probability=0.55, price=2.0) == pytest.approx(0.10)

    def test_fair_bet_is_zero(self) -> None:
        assert expected_value(probability=0.5, price=2.0) == pytest.approx(0.0)

    def test_negative_edge(self) -> None:
        assert expected_value(probability=0.45, price=2.0) == pytest.approx(-0.10)


class TestKelly:
    def test_hand_computed(self) -> None:
        # f* = (p*o - 1) / (o - 1) = (0.55*2 - 1) / 1 = 0.10
        assert kelly_fraction(probability=0.55, price=2.0) == pytest.approx(0.10)

    def test_no_edge_returns_zero(self) -> None:
        assert kelly_fraction(probability=0.45, price=2.0) == 0.0

    def test_fractional_kelly(self) -> None:
        assert kelly_fraction(probability=0.55, price=2.0, fraction=0.5) == pytest.approx(0.05)

    def test_rejects_price_at_or_below_one(self) -> None:
        with pytest.raises(ValueError):
            kelly_fraction(probability=0.5, price=1.0)

    def test_rejects_probability_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            kelly_fraction(probability=1.2, price=2.0)

    @given(
        st.floats(min_value=0.01, max_value=0.99),
        st.floats(min_value=1.01, max_value=50.0),
    )
    def test_bounded_between_zero_and_one(self, p: float, price: float) -> None:
        f = kelly_fraction(probability=p, price=price)
        assert 0.0 <= f < 1.0
