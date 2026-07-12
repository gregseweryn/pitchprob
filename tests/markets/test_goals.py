"""Markets layer tests.

The 2x2 matrix below is small enough to settle every market by hand:

    P(0-0)=0.1  P(0-1)=0.2
    P(1-0)=0.3  P(1-1)=0.4

Hand-computed truths used throughout:
  1X2: home 0.3, draw 0.5, away 0.2
  BTTS yes 0.4 · O/U 0.5: over 0.9 · O/U 1 (integer): over 0.4, push 0.5, under 0.1
  Expected goals: home 0.7, away 0.6
"""

from decimal import Decimal

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays

from pitchprob.markets import (
    AhOutcome,
    asian_handicap,
    btts,
    correct_score,
    double_chance,
    draw_no_bet,
    expected_goals,
    match_odds,
    totals,
)

M = np.array([[0.1, 0.2], [0.3, 0.4]])


@st.composite
def score_matrices(draw: st.DrawFn) -> np.ndarray:
    rows = draw(st.integers(min_value=2, max_value=7))
    cols = draw(st.integers(min_value=2, max_value=7))
    raw = draw(
        arrays(
            np.float64,
            (rows, cols),
            elements=st.floats(min_value=0.001, max_value=1.0),
        )
    )
    return raw / raw.sum()


class TestMatchOdds:
    def test_hand_computed(self) -> None:
        r = match_odds(M)
        assert r.home == pytest.approx(0.3)
        assert r.draw == pytest.approx(0.5)
        assert r.away == pytest.approx(0.2)

    @given(score_matrices())
    def test_sums_to_one(self, m: np.ndarray) -> None:
        r = match_odds(m)
        assert r.home + r.draw + r.away == pytest.approx(1.0)


class TestDoubleChanceAndDnb:
    def test_double_chance_hand_computed(self) -> None:
        r = double_chance(M)
        assert r.home_or_draw == pytest.approx(0.8)
        assert r.home_or_away == pytest.approx(0.5)
        assert r.draw_or_away == pytest.approx(0.7)

    def test_dnb_hand_computed(self) -> None:
        r = draw_no_bet(M)
        assert r.home == pytest.approx(0.6)
        assert r.away == pytest.approx(0.4)

    @given(score_matrices())
    def test_dnb_conditional_consistency(self, m: np.ndarray) -> None:
        mo, dnb = match_odds(m), draw_no_bet(m)
        assert dnb.home * (1 - mo.draw) == pytest.approx(mo.home, abs=1e-12)


class TestTotals:
    def test_half_line(self) -> None:
        r = totals(M, Decimal("0.5"))
        assert r.over == pytest.approx(0.9)
        assert r.under == pytest.approx(0.1)
        assert r.push == 0.0

    def test_integer_line_has_push(self) -> None:
        r = totals(M, Decimal("1"))
        assert r.over == pytest.approx(0.4)
        assert r.under == pytest.approx(0.1)
        assert r.push == pytest.approx(0.5)

    @given(score_matrices(), st.sampled_from(["0.5", "1", "1.5", "2", "2.5", "3.5"]))
    def test_partition(self, m: np.ndarray, line: str) -> None:
        r = totals(m, Decimal(line))
        assert r.over + r.under + r.push == pytest.approx(1.0)


class TestBttsAndCorrectScore:
    def test_btts(self) -> None:
        r = btts(M)
        assert r.yes == pytest.approx(0.4)
        assert r.no == pytest.approx(0.6)

    def test_correct_score(self) -> None:
        assert correct_score(M, 1, 1) == pytest.approx(0.4)
        assert correct_score(M, 5, 5) == 0.0

    @given(score_matrices())
    def test_correct_scores_sum_to_one(self, m: np.ndarray) -> None:
        total = sum(
            correct_score(m, h, a)
            for h in range(m.shape[0])
            for a in range(m.shape[1])
        )
        assert total == pytest.approx(1.0)


class TestExpectedGoals:
    def test_hand_computed(self) -> None:
        eg = expected_goals(M)
        assert eg.home == pytest.approx(0.7)
        assert eg.away == pytest.approx(0.6)


class TestAsianHandicap:
    def test_level_line_equals_dnb_structure(self) -> None:
        r = asian_handicap(M, Decimal("0"), "home")
        assert r.full_win == pytest.approx(0.3)
        assert r.push == pytest.approx(0.5)
        assert r.full_loss == pytest.approx(0.2)

    def test_minus_half_equals_match_win(self) -> None:
        r = asian_handicap(M, Decimal("-0.5"), "home")
        assert r.full_win == pytest.approx(0.3)
        assert r.full_loss == pytest.approx(0.7)

    def test_plus_quarter_hand_computed(self) -> None:
        r = asian_handicap(M, Decimal("0.25"), "home")
        assert r.full_win == pytest.approx(0.3)
        assert r.half_win == pytest.approx(0.5)
        assert r.full_loss == pytest.approx(0.2)

    def test_minus_quarter_hand_computed(self) -> None:
        r = asian_handicap(M, Decimal("-0.25"), "home")
        assert r.full_win == pytest.approx(0.3)
        assert r.half_loss == pytest.approx(0.5)
        assert r.full_loss == pytest.approx(0.2)

    def test_away_side_mirrors_home(self) -> None:
        home = asian_handicap(M, Decimal("-0.5"), "home")
        away = asian_handicap(M, Decimal("-0.5"), "away")
        # away with home line -0.5 wins when home fails to cover
        assert away.full_win == pytest.approx(home.full_loss)
        assert away.full_loss == pytest.approx(home.full_win)

    def test_expected_return_full_win_and_loss(self) -> None:
        out = AhOutcome(full_win=1.0, half_win=0, push=0, half_loss=0, full_loss=0)
        assert out.expected_return(1.95) == pytest.approx(1.95)
        out = AhOutcome(full_win=0, half_win=0, push=0, half_loss=0, full_loss=1.0)
        assert out.expected_return(1.95) == 0.0

    def test_expected_return_half_outcomes(self) -> None:
        # half win returns half stake at price + half stake refunded
        out = AhOutcome(full_win=0, half_win=1.0, push=0, half_loss=0, full_loss=0)
        assert out.expected_return(2.0) == pytest.approx(1.5)
        out = AhOutcome(full_win=0, half_win=0, push=0, half_loss=1.0, full_loss=0)
        assert out.expected_return(2.0) == pytest.approx(0.5)

    @given(score_matrices(), st.sampled_from(["-1.5", "-0.5", "0", "0.5", "1", "2.5"]))
    def test_outcomes_partition(self, m: np.ndarray, line: str) -> None:
        r = asian_handicap(m, Decimal(line), "home")
        assert r.full_win + r.half_win + r.push + r.half_loss + r.full_loss == pytest.approx(1.0)

    @given(
        score_matrices(),
        st.sampled_from(["-1.25", "-0.75", "-0.25", "0.25", "0.75", "1.25"]),
        st.floats(min_value=1.2, max_value=3.5),
    )
    def test_quarter_line_is_split_stake(self, m: np.ndarray, line: str, price: float) -> None:
        """Fundamental AH identity: a quarter-line bet pays exactly the average
        of the two adjacent half-line bets."""
        q = Decimal(line)
        lower, upper = q - Decimal("0.25"), q + Decimal("0.25")
        ev_q = asian_handicap(m, q, "home").expected_return(price)
        ev_lo = asian_handicap(m, lower, "home").expected_return(price)
        ev_hi = asian_handicap(m, upper, "home").expected_return(price)
        assert ev_q == pytest.approx((ev_lo + ev_hi) / 2, abs=1e-9)
