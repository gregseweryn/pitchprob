"""Probability calibration for multiclass forecasts.

Two methods behind one interface:

- :class:`TemperatureScaling` — one parameter ``T``: ``p_c ∝ p_c^(1/T)``.
  ``T > 1`` softens overconfident forecasts, ``T < 1`` sharpens timid ones.
  Parametric, monotone (never reorders outcomes), hard to overfit.
- :class:`IsotonicCalibrator` — per-class isotonic regression (one-vs-rest)
  with renormalization. Non-parametric; fixes structural miscalibration but
  needs more data and may reorder outcomes.

The production ensemble calibrates via its stacking layer (ADR 0005); these
exist for calibrating individual models and for reliability reporting.
"""

from typing import Self

import numpy as np
import numpy.typing as npt
from scipy.optimize import minimize_scalar
from sklearn.isotonic import IsotonicRegression

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

_EPS = 1e-12


def _validate(probs: FloatArray) -> FloatArray:
    p = np.asarray(probs, dtype=np.float64)
    if p.ndim != 2:
        raise ValueError(f"expected (n, classes) probabilities, got shape {p.shape}")
    return np.clip(p, _EPS, 1.0)


class TemperatureScaling:
    def __init__(self) -> None:
        self.temperature_: float | None = None

    def fit(self, probs: FloatArray, outcomes: IntArray) -> Self:
        p = _validate(probs)
        log_p = np.log(p)
        index = np.arange(len(outcomes))

        def nll(log_t: float) -> float:
            scaled = log_p / np.exp(log_t)
            scaled -= scaled.max(axis=1, keepdims=True)
            z = np.exp(scaled)
            z /= z.sum(axis=1, keepdims=True)
            return float(-np.log(np.clip(z[index, outcomes], _EPS, None)).mean())

        result = minimize_scalar(nll, bounds=(-3.0, 3.0), method="bounded")
        self.temperature_ = float(np.exp(result.x))
        return self

    def transform(self, probs: FloatArray) -> FloatArray:
        if self.temperature_ is None:
            raise ValueError("fit() before transform()")
        p = _validate(probs)
        scaled = np.log(p) / self.temperature_
        scaled -= scaled.max(axis=1, keepdims=True)
        z = np.exp(scaled)
        result: FloatArray = z / z.sum(axis=1, keepdims=True)
        return result


class IsotonicCalibrator:
    def __init__(self) -> None:
        self._per_class: list[IsotonicRegression] | None = None

    def fit(self, probs: FloatArray, outcomes: IntArray) -> Self:
        p = _validate(probs)
        self._per_class = []
        for cls in range(p.shape[1]):
            iso = IsotonicRegression(y_min=_EPS, y_max=1.0, out_of_bounds="clip")
            iso.fit(p[:, cls], (outcomes == cls).astype(np.float64))
            self._per_class.append(iso)
        return self

    def transform(self, probs: FloatArray) -> FloatArray:
        if self._per_class is None:
            raise ValueError("fit() before transform()")
        p = _validate(probs)
        calibrated = np.column_stack(
            [iso.predict(p[:, cls]) for cls, iso in enumerate(self._per_class)]
        )
        calibrated = np.clip(calibrated, _EPS, None)
        result: FloatArray = calibrated / calibrated.sum(axis=1, keepdims=True)
        return result
