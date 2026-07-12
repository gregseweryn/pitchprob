"""Forecast quality metrics with hand-computed reference values.

Outcome encoding everywhere: 0 = home win, 1 = draw, 2 = away win.
"""

import numpy as np
import pytest

from pitchprob.evaluation.metrics import (
    brier_score,
    expected_calibration_error,
    log_loss,
    ranked_probability_score,
    reliability_table,
)

PROBS = np.array(
    [
        [0.5, 0.3, 0.2],
        [0.1, 0.2, 0.7],
    ]
)
OUTCOMES = np.array([0, 2])


class TestLogLoss:
    def test_hand_computed(self) -> None:
        expected = -(np.log(0.5) + np.log(0.7)) / 2
        assert log_loss(OUTCOMES, PROBS) == pytest.approx(expected)

    def test_perfect_forecast_is_zero(self) -> None:
        probs = np.array([[1.0, 0.0, 0.0]])
        assert log_loss(np.array([0]), probs) == pytest.approx(0.0, abs=1e-9)

    def test_uniform_forecast(self) -> None:
        probs = np.full((4, 3), 1 / 3)
        assert log_loss(np.array([0, 1, 2, 0]), probs) == pytest.approx(np.log(3))


class TestBrier:
    def test_perfect_forecast_is_zero(self) -> None:
        assert brier_score(np.array([1]), np.array([[0.0, 1.0, 0.0]])) == 0.0

    def test_hand_computed(self) -> None:
        # [0.5,0.3,0.2] vs true home: 0.25 + 0.09 + 0.04 = 0.38
        assert brier_score(np.array([0]), np.array([[0.5, 0.3, 0.2]])) == pytest.approx(0.38)

    def test_worst_case_is_two(self) -> None:
        assert brier_score(np.array([0]), np.array([[0.0, 1.0, 0.0]])) == pytest.approx(2.0)


class TestRps:
    def test_perfect_forecast_is_zero(self) -> None:
        assert ranked_probability_score(
            np.array([0]), np.array([[1.0, 0.0, 0.0]])
        ) == pytest.approx(0.0)

    def test_hand_computed(self) -> None:
        # cum p = (0.5, 0.8), cum true = (1, 1): ((0.5-1)^2 + (0.8-1)^2) / 2 = 0.145
        assert ranked_probability_score(
            np.array([0]), np.array([[0.5, 0.3, 0.2]])
        ) == pytest.approx(0.145)

    def test_ordering_sensitivity(self) -> None:
        """RPS (unlike Brier) punishes putting mass on the *far* outcome:
        predicting away when home happens is worse than predicting draw."""
        true_home = np.array([0])
        mass_on_draw = np.array([[0.2, 0.6, 0.2]])
        mass_on_away = np.array([[0.2, 0.2, 0.6]])
        assert ranked_probability_score(true_home, mass_on_away) > ranked_probability_score(
            true_home, mass_on_draw
        )


class TestCalibration:
    def test_perfectly_calibrated_bins(self) -> None:
        rng = np.random.default_rng(42)
        p = rng.uniform(0.05, 0.95, size=20_000)
        hits = (rng.uniform(size=p.size) < p).astype(np.int64)
        ece = expected_calibration_error(hits, p, n_bins=10)
        assert ece < 0.02

    def test_badly_miscalibrated(self) -> None:
        p = np.full(1000, 0.9)
        hits = np.zeros(1000, dtype=np.int64)  # 90% claimed, 0% observed
        assert expected_calibration_error(hits, p, n_bins=10) == pytest.approx(0.9)

    def test_reliability_table_shape(self) -> None:
        p = np.array([0.1, 0.15, 0.85, 0.9])
        hits = np.array([0, 0, 1, 1])
        table = reliability_table(hits, p, n_bins=10)
        assert set(table.columns) >= {"bin_mid", "mean_predicted", "observed_rate", "count"}
        assert int(table["count"].sum()) == 4
