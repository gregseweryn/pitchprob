"""Block-bootstrap significance for backtest comparisons (ADR 0010).

Units (matches or bets) are never resampled individually: fixtures inside one
match-week share teams, conditions and market state, so the week is the
exchangeable unit. Hand-computed cases pin the statistics; hypothesis
properties pin the invariants.
"""

from datetime import date

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pitchprob.evaluation.significance import (
    block_bootstrap_mean,
    block_bootstrap_ratio,
    block_bootstrap_ratio_delta,
    week_block_labels,
)


class TestWeekBlockLabels:
    def test_iso_year_boundary(self) -> None:
        # 2021-01-03 is the Sunday of ISO week 2020-W53; the next day opens
        # ISO 2021-W01. Calendar-year labels would glue these together.
        labels = week_block_labels([date(2021, 1, 3), date(2021, 1, 4)])
        assert labels.tolist() == [202053, 202101]

    def test_one_round_one_label(self) -> None:
        friday, saturday, sunday = (
            date(2024, 8, 16),
            date(2024, 8, 17),
            date(2024, 8, 18),
        )
        labels = week_block_labels([friday, saturday, sunday])
        assert len(set(labels.tolist())) == 1
        assert labels[0] == 202433


class TestBlockBootstrapMean:
    def test_point_estimate_is_the_plain_mean(self) -> None:
        summary = block_bootstrap_mean(
            [1.0, 2.0, 3.0, 4.0], ["a", "a", "b", "b"], n_boot=100, seed=1
        )
        assert summary.point == pytest.approx(2.5)
        assert summary.n_units == 4
        assert summary.n_blocks == 2
        assert summary.n_boot == 100

    def test_identical_values_give_zero_width_interval(self) -> None:
        summary = block_bootstrap_mean(
            [0.5] * 6, [1, 1, 2, 2, 3, 3], n_boot=500, seed=2
        )
        assert summary.lo == pytest.approx(0.5)
        assert summary.hi == pytest.approx(0.5)
        # Every resampled mean sits at 0.5 > 0: as significant as n_boot allows.
        assert summary.p_value <= 0.01

    def test_same_seed_is_deterministic(self) -> None:
        values = [0.3, -0.1, 0.7, 0.2, -0.4, 0.6]
        blocks = [1, 1, 2, 2, 3, 3]
        first = block_bootstrap_mean(values, blocks, n_boot=300, seed=7)
        second = block_bootstrap_mean(values, blocks, n_boot=300, seed=7)
        assert first == second

    def test_strong_signal_is_significant(self) -> None:
        # 40 units in 20 blocks, all deltas near +1: the interval must sit
        # clear of zero and the p-value must be small.
        values = [1.0 + 0.01 * ((i % 3) - 1) for i in range(40)]
        blocks = [i // 2 for i in range(40)]
        summary = block_bootstrap_mean(values, blocks, n_boot=1000, seed=3)
        assert summary.lo > 0.9
        assert summary.p_value < 0.05

    def test_rejects_mismatched_lengths(self) -> None:
        with pytest.raises(ValueError):
            block_bootstrap_mean([1.0, 2.0], [1], n_boot=10, seed=0)

    def test_rejects_fewer_than_two_blocks(self) -> None:
        with pytest.raises(ValueError):
            block_bootstrap_mean([1.0, 2.0], [1, 1], n_boot=10, seed=0)

    def test_rejects_empty_input(self) -> None:
        with pytest.raises(ValueError):
            block_bootstrap_mean([], [], n_boot=10, seed=0)


class TestBlockBootstrapRatio:
    def test_point_estimate_is_ratio_of_sums(self) -> None:
        summary = block_bootstrap_ratio(
            [2.0, 1.0], [4.0, 1.0], [0, 1], n_boot=100, seed=1
        )
        assert summary.point == pytest.approx(3.0 / 5.0)

    def test_roi_semantics_flat_stakes(self) -> None:
        # Six unit-stake bets alternating +1/-1: ROI is exactly zero and the
        # resampled distribution straddles it.
        pnl = [1.0, -1.0, 1.0, -1.0, 1.0, -1.0]
        stakes = [1.0] * 6
        blocks = [1, 1, 2, 2, 3, 3]
        summary = block_bootstrap_ratio(pnl, stakes, blocks, n_boot=500, seed=4)
        assert summary.point == pytest.approx(0.0)
        assert summary.lo <= 0.0 <= summary.hi

    def test_rejects_zero_total_stake(self) -> None:
        with pytest.raises(ValueError):
            block_bootstrap_ratio([1.0, 1.0], [0.0, 0.0], [1, 2], n_boot=10, seed=0)


class TestBlockBootstrapRatioDelta:
    """Inputs are per-block aggregates aligned on the union of blocks —
    zeros where a strategy placed no bets that week."""

    def test_identical_strategies_are_a_perfect_null(self) -> None:
        num = [1.0, -2.0, 0.5]
        den = [3.0, 4.0, 2.0]
        summary = block_bootstrap_ratio_delta(
            num, den, num, den, n_boot=300, seed=5
        )
        assert summary.point == pytest.approx(0.0)
        assert summary.lo == pytest.approx(0.0)
        assert summary.hi == pytest.approx(0.0)
        assert summary.p_value == pytest.approx(1.0)

    def test_hand_computed_delta(self) -> None:
        # A: 4 profit on 4 staked (ROI 1.0); B: 1 profit on 4 staked (0.25).
        summary = block_bootstrap_ratio_delta(
            [2.0, 2.0], [2.0, 2.0], [0.0, 1.0], [2.0, 2.0], n_boot=100, seed=6
        )
        assert summary.point == pytest.approx(0.75)
        assert summary.n_blocks == 2

    def test_sign_flips_when_strategies_swap(self) -> None:
        a_num, a_den = [2.0, 2.0], [2.0, 2.0]
        b_num, b_den = [0.0, 1.0], [2.0, 2.0]
        forward = block_bootstrap_ratio_delta(
            a_num, a_den, b_num, b_den, n_boot=200, seed=8
        )
        backward = block_bootstrap_ratio_delta(
            b_num, b_den, a_num, a_den, n_boot=200, seed=8
        )
        assert forward.point == pytest.approx(-backward.point)

    def test_rejects_strategy_with_no_stake(self) -> None:
        with pytest.raises(ValueError):
            block_bootstrap_ratio_delta(
                [1.0, 1.0], [1.0, 1.0], [0.0, 0.0], [0.0, 0.0], n_boot=10, seed=0
            )


class TestProperties:
    @given(
        values=st.lists(
            st.floats(-10.0, 10.0, allow_nan=False), min_size=4, max_size=40
        ),
        n_blocks=st.integers(2, 5),
        seed=st.integers(0, 10_000),
    )
    @settings(max_examples=25, deadline=None)
    def test_interval_is_ordered_and_counts_are_right(
        self, values: list[float], n_blocks: int, seed: int
    ) -> None:
        blocks = [i % n_blocks for i in range(len(values))]
        summary = block_bootstrap_mean(values, blocks, n_boot=200, seed=seed)
        assert summary.lo <= summary.hi
        assert np.isfinite(summary.point)
        assert summary.n_units == len(values)
        assert summary.n_blocks == len(set(blocks))
        assert 0.0 < summary.p_value <= 1.0

    @given(scale=st.floats(0.5, 3.0, allow_nan=False))
    @settings(max_examples=25, deadline=None)
    def test_mean_summary_is_scale_equivariant(self, scale: float) -> None:
        values = [0.4, -0.2, 0.9, 0.1, -0.6, 0.3]
        blocks = [1, 1, 2, 2, 3, 3]
        base = block_bootstrap_mean(values, blocks, n_boot=200, seed=9)
        scaled = block_bootstrap_mean(
            [v * scale for v in values], blocks, n_boot=200, seed=9
        )
        assert scaled.point == pytest.approx(base.point * scale)
        assert scaled.lo == pytest.approx(base.lo * scale)
        assert scaled.hi == pytest.approx(base.hi * scale)
