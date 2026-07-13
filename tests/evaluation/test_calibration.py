"""Calibration layer tests on synthetic data with known miscalibration."""

import numpy as np
import pytest

from pitchprob.evaluation.calibration import IsotonicCalibrator, TemperatureScaling
from pitchprob.evaluation.metrics import expected_calibration_error, log_loss


def synthetic(n: int = 20_000, sharpen: float = 1.0, seed: int = 3):
    """True probs from a Dirichlet; outcomes sampled from the truth; the
    *reported* probs are sharpened (power > 1 = overconfident)."""
    rng = np.random.default_rng(seed)
    true = rng.dirichlet(alpha=(4.0, 3.0, 3.0), size=n)
    outcomes = np.array([rng.choice(3, p=p) for p in true], dtype=np.int64)
    reported = true**sharpen
    reported /= reported.sum(axis=1, keepdims=True)
    return reported, outcomes


class TestTemperatureScaling:
    def test_recovers_overconfidence(self) -> None:
        probs, outcomes = synthetic(sharpen=2.0)
        cal = TemperatureScaling().fit(probs, outcomes)
        assert cal.temperature_ > 1.5  # needs strong softening

        fixed = cal.transform(probs)
        assert log_loss(outcomes, fixed) < log_loss(outcomes, probs)
        ece_before = expected_calibration_error((outcomes == 0).astype(np.int64), probs[:, 0])
        ece_after = expected_calibration_error((outcomes == 0).astype(np.int64), fixed[:, 0])
        assert ece_after < ece_before

    def test_underconfidence_sharpens(self) -> None:
        probs, outcomes = synthetic(sharpen=0.5)
        cal = TemperatureScaling().fit(probs, outcomes)
        assert cal.temperature_ < 0.8

    def test_near_identity_when_calibrated(self) -> None:
        probs, outcomes = synthetic(sharpen=1.0)
        cal = TemperatureScaling().fit(probs, outcomes)
        assert 0.9 < cal.temperature_ < 1.1
        fixed = cal.transform(probs)
        assert np.abs(fixed - probs).max() < 0.05

    def test_rows_sum_to_one_and_order_preserved(self) -> None:
        probs, outcomes = synthetic(sharpen=2.0, n=2000)
        fixed = TemperatureScaling().fit(probs, outcomes).transform(probs)
        assert fixed.sum(axis=1) == pytest.approx(np.ones(len(fixed)))
        assert (fixed.argmax(axis=1) == probs.argmax(axis=1)).all()


class TestIsotonicCalibrator:
    def test_fixes_overconfidence(self) -> None:
        probs, outcomes = synthetic(sharpen=2.0)
        cal = IsotonicCalibrator().fit(probs, outcomes)
        fixed = cal.transform(probs)
        assert log_loss(outcomes, fixed) < log_loss(outcomes, probs)
        ece_before = expected_calibration_error((outcomes == 0).astype(np.int64), probs[:, 0])
        ece_after = expected_calibration_error((outcomes == 0).astype(np.int64), fixed[:, 0])
        assert ece_after < ece_before

    def test_rows_sum_to_one_and_stay_positive(self) -> None:
        probs, outcomes = synthetic(sharpen=2.0, n=5000)
        fixed = IsotonicCalibrator().fit(probs, outcomes).transform(probs)
        assert fixed.sum(axis=1) == pytest.approx(np.ones(len(fixed)))
        assert (fixed > 0).all()
