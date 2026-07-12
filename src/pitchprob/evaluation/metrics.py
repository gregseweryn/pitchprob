"""Probabilistic forecast metrics.

Outcome encoding for 1X2 markets: 0 = home win, 1 = draw, 2 = away win.
The ordering matters for the ranked probability score, which — unlike Brier —
penalizes mass placed on the *far* outcome more than on the adjacent one and
is the standard headline metric in football forecasting.
"""

import numpy as np
import numpy.typing as npt
import pandas as pd

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

_EPS = 1e-15


def _one_hot(outcomes: IntArray, n_classes: int) -> FloatArray:
    onehot = np.zeros((len(outcomes), n_classes), dtype=np.float64)
    onehot[np.arange(len(outcomes)), outcomes] = 1.0
    return onehot


def log_loss(outcomes: IntArray, probs: FloatArray) -> float:
    """Mean negative log probability of the realized outcome."""
    realized = probs[np.arange(len(outcomes)), outcomes]
    return float(-np.log(np.clip(realized, _EPS, None)).mean())


def brier_score(outcomes: IntArray, probs: FloatArray) -> float:
    """Multiclass Brier: mean over samples of the summed squared error
    against the one-hot outcome (range 0..2)."""
    onehot = _one_hot(outcomes, probs.shape[1])
    return float(((probs - onehot) ** 2).sum(axis=1).mean())


def ranked_probability_score(outcomes: IntArray, probs: FloatArray) -> float:
    """RPS over ordered outcomes: mean of sum of squared CDF differences,
    normalized by (K - 1)."""
    onehot = _one_hot(outcomes, probs.shape[1])
    cum_diff = probs.cumsum(axis=1) - onehot.cumsum(axis=1)
    per_sample = (cum_diff[:, :-1] ** 2).sum(axis=1) / (probs.shape[1] - 1)
    return float(per_sample.mean())


def reliability_table(
    hits: IntArray, probabilities: FloatArray, *, n_bins: int = 10
) -> pd.DataFrame:
    """Bin predictions by claimed probability; report observed hit rates."""
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bin_index = np.clip(np.digitize(probabilities, edges) - 1, 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        mask = bin_index == b
        count = int(mask.sum())
        rows.append(
            {
                "bin_mid": float((edges[b] + edges[b + 1]) / 2),
                "mean_predicted": float(probabilities[mask].mean()) if count else float("nan"),
                "observed_rate": float(hits[mask].mean()) if count else float("nan"),
                "count": count,
            }
        )
    return pd.DataFrame(rows)


def expected_calibration_error(
    hits: IntArray, probabilities: FloatArray, *, n_bins: int = 10
) -> float:
    """Count-weighted mean absolute gap between claimed and observed rates."""
    table = reliability_table(hits, probabilities, n_bins=n_bins)
    populated = table[table["count"] > 0]
    weights = populated["count"] / populated["count"].sum()
    gaps = (populated["mean_predicted"] - populated["observed_rate"]).abs()
    return float((weights * gaps).sum())
