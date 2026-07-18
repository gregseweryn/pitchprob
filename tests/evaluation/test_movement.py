"""Open→close movement diagnostics (Phase 1): hand-computed geometry.

The central quantities: the movement vector (close fair minus open fair), a
reference direction (model minus open, or realized outcome minus open), and
their dot product — positive means the market moved toward the reference.
"""

import numpy as np
import pytest

from pitchprob.evaluation.movement import (
    bucketed_agreement,
    directional_agreement,
    movement_vectors,
)


class TestMovementVectors:
    def test_difference_and_zero_sum(self) -> None:
        open_fair = np.array([[0.50, 0.30, 0.20]])
        close_fair = np.array([[0.55, 0.28, 0.17]])
        movement = movement_vectors(open_fair, close_fair)
        assert movement[0] == pytest.approx([0.05, -0.02, -0.03])
        assert movement.sum(axis=1)[0] == pytest.approx(0.0)

    def test_shape_mismatch_raises(self) -> None:
        with pytest.raises(ValueError):
            movement_vectors(np.zeros((2, 3)), np.zeros((3, 3)))


class TestDirectionalAgreement:
    def test_hand_computed_dot_product(self) -> None:
        movement = np.array([[0.05, -0.02, -0.03]])
        # model sat above the open on home by 0.10, below on draw/away
        reference = np.array([[0.10, -0.04, -0.06]])
        dots = directional_agreement(movement, reference)
        expected = 0.05 * 0.10 + (-0.02) * (-0.04) + (-0.03) * (-0.06)
        assert dots[0] == pytest.approx(expected)

    def test_orthogonal_reference_is_zero(self) -> None:
        movement = np.array([[0.05, -0.05, 0.0]])
        reference = np.array([[0.0, 0.0, 0.0]])
        assert directional_agreement(movement, reference)[0] == pytest.approx(0.0)

    def test_away_from_reference_is_negative(self) -> None:
        movement = np.array([[-0.05, 0.02, 0.03]])
        reference = np.array([[0.10, -0.04, -0.06]])
        assert directional_agreement(movement, reference)[0] < 0.0


class TestBucketedAgreement:
    def test_buckets_by_reference_magnitude(self) -> None:
        # Four matches: two with tiny model-market divergence, two large.
        # Movement agrees with the reference exactly when divergence is large.
        dots = np.array([-0.001, -0.002, 0.02, 0.03])
        magnitudes = np.array([0.01, 0.02, 0.30, 0.40])
        table = bucketed_agreement(dots, magnitudes, n_buckets=2)
        assert len(table) == 2
        low, high = table.iloc[0], table.iloc[1]
        assert low["n"] == 2 and high["n"] == 2
        assert low["p_toward"] == pytest.approx(0.0)
        assert high["p_toward"] == pytest.approx(1.0)
        assert high["mean_dot"] == pytest.approx(0.025)

    def test_rejects_mismatched_lengths(self) -> None:
        with pytest.raises(ValueError):
            bucketed_agreement(np.zeros(3), np.zeros(4), n_buckets=2)
